from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from urllib.parse import urlsplit

import httpx

from app.schemas import LinkingAnalyzeRequest, LinkingAnalyzeResponse
from app.services.fetcher import AsyncFetcher, FetchSession
from app.services.frontier import CrawlNode, apply_sitemap_bonus, prioritize, score_link
from app.services.internal_linking_models import (
    CrawlDiagnosticsSnapshot,
    LIVE_SITEMAP_STRATEGY,
    RobotsSnapshot,
    SITEMAP_WAIT_TIMEOUT_SECONDS,
    SitemapSnapshot,
)
from app.services.internal_linking_response import InternalLinkingResponseMixin
from app.services.internal_linking_runtime import InternalLinkingRuntimeMixin
from app.services.internal_linking_verification import InternalLinkingVerificationMixin
from app.services.link_placement import CrawledPageSnapshot, LinkPlacementRecommender
from app.services.llm_reranker import PlacementRecommendationReranker
from app.services.llm_summary import LinkingAnalysisMessageGenerator
from app.services.matcher import SearchTarget, extract_url_terms
from app.services.parser import (
    ParsedPage,
    canonical_host,
    get_site_root,
    is_internal_url,
    normalize_url,
    parse_html,
    parse_robots_txt,
    parse_sitemap,
)
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class InternalLinkingAnalyzer(
    InternalLinkingResponseMixin,
    InternalLinkingVerificationMixin,
    InternalLinkingRuntimeMixin,
):
    def __init__(
        self,
        request: LinkingAnalyzeRequest,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._requested_target_url = normalize_url(str(request.target_url))
        self._start_url = self._resolve_start_url(target_url=self._requested_target_url)
        self._allowed_host = canonical_host(httpx.URL(self._start_url).host if self._start_url else None)
        self._requested_target_title = None
        self._requested_target_text = None
        self._target = SearchTarget(
            url=self._requested_target_url,
            title=self._requested_target_title,
            text=self._requested_target_text,
        )
        self._fetcher = AsyncFetcher(
            timeout_seconds=settings.request_timeout_seconds,
            retry_count=settings.request_retry_count,
            transport=transport,
        )
        self._placement_recommender = self._build_placement_recommender()
        self._placement_reranker = PlacementRecommendationReranker()
        self._message_generator = LinkingAnalysisMessageGenerator()
        self._semaphore = asyncio.Semaphore(settings.crawl_concurrency)
        self._deadline_started_at: float | None = None
        self._crawl_diagnostics = CrawlDiagnosticsSnapshot(crawl_max_depth=settings.crawl_max_depth)
        self._robots_snapshot = RobotsSnapshot(obeyed=settings.obey_robots_txt)
        self._robots_policy = None
        self._html_403_branch_counts: dict[str, int] = {}
        self._html_403_blocked_branches: set[str] = set()

    @staticmethod
    def _resolve_start_url(*, target_url: str | None) -> str | None:
        if not target_url:
            return None
        return normalize_url(get_site_root(target_url))

    def _build_placement_recommender(self) -> LinkPlacementRecommender:
        return LinkPlacementRecommender(
            target=self._target,
            start_url=self._start_url or "",
            good_depth_threshold=settings.good_depth_threshold,
        )

    def _score_discovered_link(self, url: str, anchor_text: str) -> int:
        score = score_link(url, anchor_text, self._target.priority_terms)
        if self._target.url:
            score += self._candidate_branch_bonus(url, {self._target.url})
        return score

    def _replace_target(
        self,
        *,
        primary_url: str | None = None,
        title: str | None = None,
        equivalent_urls: set[str] | None = None,
    ) -> None:
        resolved_target_url = primary_url or self._requested_target_url
        normalized_equivalents = tuple(
            sorted({url for url in (equivalent_urls or set()) if url and url != resolved_target_url})
        )
        self._target = SearchTarget(
            url=resolved_target_url,
            title=title if title is not None else self._requested_target_title,
            text=self._requested_target_text,
            equivalent_urls=normalized_equivalents,
        )
        self._placement_recommender = self._build_placement_recommender()

    def _is_allowed_by_robots(self, url: str) -> bool:
        if not settings.obey_robots_txt or self._robots_policy is None:
            return True
        allowed = self._robots_policy.is_allowed(url)
        if not allowed:
            self._robots_snapshot.blocked_urls.add(url)
        return allowed

    async def _collect_robots_snapshot(self, client: FetchSession) -> None:
        if not self._start_url:
            return

        robots_url = normalize_url("robots.txt", get_site_root(self._start_url), allow_ignored_extensions=True)
        if not robots_url:
            return

        self._robots_snapshot.checked = True
        async with self._semaphore:
            document = await self._fetcher.fetch(
                client,
                robots_url,
                render_html=False,
                total_timeout_seconds=self._remaining_fetch_budget_seconds(),
            )

        if document is None:
            logger.info("robots.txt is unavailable or could not be fetched: %s", robots_url)
            return

        self._robots_snapshot.available = True
        self._robots_policy = parse_robots_txt(
            document.body,
            get_site_root(self._start_url),
            self._allowed_host,
            settings.robots_user_agent,
        )
        self._robots_snapshot.sitemap_urls.update(self._robots_policy.sitemap_urls)
        logger.info(
            "robots.txt loaded: start_url=%s sitemap_urls=%s",
            self._start_url,
            len(self._robots_snapshot.sitemap_urls),
        )

    async def _resolve_target_metadata(self, client: FetchSession) -> int:
        if not self._requested_target_url or not is_internal_url(self._requested_target_url, self._allowed_host):
            return 0
        if not self._is_allowed_by_robots(self._requested_target_url):
            logger.warning("Requested target URL is blocked by robots.txt: %s", self._requested_target_url)
            return 0
        document = await self._fetcher.fetch(
            client,
            self._requested_target_url,
            total_timeout_seconds=self._remaining_fetch_budget_seconds(),
        )
        if document is None:
            logger.warning("Failed to fetch requested target URL metadata: %s", self._requested_target_url)
            return 0
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
            logger.warning(
                "Requested target URL resolved outside of the allowed host: requested=%s final=%s",
                self._requested_target_url,
                document.final_url,
            )
            return 0
        page = parse_html(document.body, normalized_final_url, self._allowed_host)
        equivalent_urls = {self._requested_target_url, normalized_final_url}
        resolved_target_url = normalized_final_url
        if page.canonical_url and is_internal_url(page.canonical_url, self._allowed_host):
            equivalent_urls.add(page.canonical_url)
            resolved_target_url = page.canonical_url
        resolved_title = self._requested_target_title or page.h1 or page.title or None
        self._replace_target(
            primary_url=resolved_target_url,
            title=resolved_title,
            equivalent_urls=equivalent_urls,
        )
        return 1

    async def analyze(self) -> LinkingAnalyzeResponse:
        if not self._start_url:
            raise ValueError("Не удалось определить стартовую страницу сайта из target_url.")

        started_at = perf_counter()
        self._deadline_started_at = started_at
        self._crawl_diagnostics = CrawlDiagnosticsSnapshot(crawl_max_depth=settings.crawl_max_depth)
        self._robots_snapshot = RobotsSnapshot(obeyed=settings.obey_robots_txt)
        self._robots_policy = None
        self._html_403_branch_counts = {}
        self._html_403_blocked_branches = set()
        pages_fetched = 0
        discovered_urls: set[str] = {self._start_url}
        discovered_depths: dict[str, int] = {self._start_url: 0}
        crawled_pages: dict[str, CrawledPageSnapshot] = {}
        search_depth_limit = settings.crawl_max_depth
        logger.info(
            "Starting internal linking analysis: start_url=%s target_url=%s depth_limit=%s timeout=%s retry_count=%s",
            self._start_url,
            self._requested_target_url,
            search_depth_limit,
            settings.request_timeout_seconds,
            settings.request_retry_count,
        )

        async with self._fetcher.create_client() as client:
            await self._collect_robots_snapshot(client)
            pages_fetched += await self._resolve_target_metadata(client)
            sitemap = SitemapSnapshot(started_at=perf_counter())
            sitemap_task = asyncio.create_task(self._collect_sitemap_snapshot(client, sitemap))
            try:
                current_level: list[CrawlNode] = []
                if self._is_allowed_by_robots(self._start_url):
                    current_level.append(CrawlNode(url=self._start_url, depth=0, path=[self._start_url]))
                else:
                    logger.warning("Start URL is blocked by robots.txt: %s", self._start_url)
                while current_level:
                    if self._budget_exhausted():
                        logger.warning("Analysis budget exhausted during BFS traversal: start_url=%s", self._start_url)
                        break
                    level_candidates: dict[str, CrawlNode] = {}
                    limited_level = self._limit_nodes(
                        current_level,
                        depth=current_level[0].depth if current_level else 0,
                    )
                    tasks = [asyncio.create_task(self._fetch_node(client, node)) for node in limited_level]
                    try:
                        for task in asyncio.as_completed(tasks):
                            node, page = await task
                            if self._budget_exhausted():
                                self._cancel_pending(tasks)
                                break
                            if page is None:
                                continue
                            pages_fetched += 1
                            snapshot = self._placement_recommender.build_snapshot(
                                url=page.url,
                                title=page.title,
                                h1=page.h1,
                                depth=node.depth,
                                text=page.text,
                                is_indexable=page.is_indexable,
                                links_to_target=bool(self._target.url and any(self._target.url_matches(link.url) for link in page.links)),
                            )
                            self._remember_crawled_page(crawled_pages, snapshot)
                            matched_by = self._target.page_matches(page.url, page.title, page.text)
                            if matched_by:
                                self._cancel_pending(tasks)
                                return await self._build_response(
                                    found=True,
                                    matched_by=matched_by,
                                    steps_to_target=node.depth,
                                    path=node.path,
                                    pages_fetched=pages_fetched,
                                    pages_discovered=len(discovered_urls),
                                    sitemap_checked=sitemap.checked,
                                    found_in_sitemap=sitemap.found_target,
                                    strategy=LIVE_SITEMAP_STRATEGY,
                                    timings=self._build_timings(started_at=started_at, finished_at=perf_counter(), found=True, sitemap=sitemap),
                                    client=client,
                                    crawled_pages=crawled_pages,
                                    discovered_depths=discovered_depths,
                                    sitemap_page_urls=sitemap.page_urls,
                                    search_depth_limit=search_depth_limit,
                                )
                            if node.depth >= search_depth_limit:
                                self._crawl_diagnostics.depth_cutoff = True
                                continue
                            for link in page.links:
                                if not is_internal_url(link.url, self._allowed_host):
                                    continue
                                if not self._is_allowed_by_robots(link.url):
                                    continue
                                if self._is_html_403_branch_blocked(link.url):
                                    continue
                                if self._target.url_matches(link.url):
                                    self._cancel_pending(tasks)
                                    return await self._build_response(
                                        found=True,
                                        matched_by=["url"],
                                        steps_to_target=node.depth + 1,
                                        path=node.path + [link.url],
                                        pages_fetched=pages_fetched,
                                        pages_discovered=len(discovered_urls),
                                        sitemap_checked=sitemap.checked,
                                        found_in_sitemap=sitemap.found_target,
                                        strategy=LIVE_SITEMAP_STRATEGY,
                                        timings=self._build_timings(started_at=started_at, finished_at=perf_counter(), found=True, sitemap=sitemap),
                                        client=client,
                                        crawled_pages=crawled_pages,
                                        discovered_depths=discovered_depths,
                                        sitemap_page_urls=sitemap.page_urls,
                                        search_depth_limit=search_depth_limit,
                                    )
                                if link.url in discovered_urls:
                                    self._remember_depth(discovered_depths, link.url, node.depth + 1)
                                    continue
                                candidate = CrawlNode(
                                    url=link.url,
                                    depth=node.depth + 1,
                                    path=node.path + [link.url],
                                    score=self._score_discovered_link(link.url, link.anchor_text),
                                )
                                self._remember_depth(discovered_depths, link.url, candidate.depth)
                                existing = level_candidates.get(link.url)
                                if existing is None or candidate.score > existing.score:
                                    level_candidates[link.url] = candidate
                        next_level = list(level_candidates.values())
                        apply_sitemap_bonus(next_level, sitemap.page_urls)
                        discovered_urls.update(level_candidates.keys())
                        current_level = self._limit_nodes(
                            prioritize(next_level),
                            depth=next_level[0].depth if next_level else 0,
                        )
                    finally:
                        await self._gather_tasks_with_logging(tasks, context="crawl level fetch")

                target_verification = await self._verify_target_path(
                    client=client,
                    crawled_pages=crawled_pages,
                    discovered_urls=discovered_urls,
                    max_depth=search_depth_limit,
                    reserve_seconds=self._recommendation_budget_reserve_seconds(),
                )
                pages_fetched += target_verification.pages_fetched
                if target_verification.steps_to_target is not None:
                    return await self._build_response(
                        found=True,
                        matched_by=["url"],
                        steps_to_target=target_verification.steps_to_target,
                        path=target_verification.path,
                        pages_fetched=pages_fetched,
                        pages_discovered=len(discovered_urls),
                        sitemap_checked=sitemap.checked,
                        found_in_sitemap=sitemap.found_target,
                        strategy=LIVE_SITEMAP_STRATEGY,
                        timings=self._build_timings(started_at=started_at, finished_at=perf_counter(), found=True, sitemap=sitemap),
                        client=client,
                        crawled_pages=crawled_pages,
                        discovered_depths=discovered_depths,
                        sitemap_page_urls=sitemap.page_urls,
                        search_depth_limit=search_depth_limit,
                    )

                await self._await_sitemap_for_recommendations(
                    sitemap_task,
                    sitemap,
                    pages_fetched=pages_fetched,
                    reserve_seconds=self._recommendation_budget_reserve_seconds(),
                )
                return await self._build_response(
                    found=False,
                    matched_by=[],
                    steps_to_target=None,
                    path=[],
                    pages_fetched=pages_fetched,
                    pages_discovered=len(discovered_urls),
                    sitemap_checked=sitemap.checked,
                    found_in_sitemap=sitemap.found_target,
                    strategy=LIVE_SITEMAP_STRATEGY,
                    timings=self._build_timings(started_at=started_at, finished_at=perf_counter(), found=False, sitemap=sitemap),
                    client=client,
                    crawled_pages=crawled_pages,
                    discovered_depths=discovered_depths,
                    sitemap_page_urls=sitemap.page_urls,
                    search_depth_limit=search_depth_limit,
                )
            finally:
                if not sitemap_task.done():
                    sitemap_task.cancel()
                await self._gather_tasks_with_logging([sitemap_task], context="sitemap collection")

    async def _await_sitemap_for_recommendations(
        self,
        sitemap_task: asyncio.Task,
        sitemap: SitemapSnapshot,
        *,
        pages_fetched: int,
        reserve_seconds: float = 0.0,
    ) -> None:
        if sitemap.completed or sitemap_task.done():
            return
        timeout = SITEMAP_WAIT_TIMEOUT_SECONDS
        if pages_fetched == 0:
            timeout = max(timeout, settings.request_timeout_seconds)
        remaining_budget = self._remaining_budget_seconds()
        if remaining_budget is not None:
            usable_budget = remaining_budget - reserve_seconds
            if usable_budget <= 0:
                logger.info("Skipping sitemap wait because no budget remains for recommendations.")
                return
            timeout = min(timeout, usable_budget)
        try:
            await asyncio.wait_for(asyncio.shield(sitemap_task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.info("Timed out while waiting for sitemap recommendations: timeout=%.3fs", timeout)
            return

    async def _collect_sitemap_snapshot(self, client: FetchSession, sitemap: SitemapSnapshot) -> None:
        sitemap_queue = list(self._robots_snapshot.sitemap_urls)
        fallback_sitemap = normalize_url("sitemap.xml", get_site_root(self._start_url), allow_ignored_extensions=True)
        if fallback_sitemap and fallback_sitemap not in sitemap_queue:
            sitemap_queue.append(fallback_sitemap)
        self._prioritize_sitemap_queue(sitemap_queue, checked=set())
        checked: set[str] = set()
        try:
            while sitemap_queue:
                if self._budget_exhausted() or self._sitemap_budget_exhausted(sitemap):
                    logger.info("Stopping sitemap collection because sitemap budget is exhausted.")
                    return
                if len(checked) >= settings.sitemap_max_files:
                    logger.info("Stopping sitemap collection because sitemap file limit is reached: limit=%s", settings.sitemap_max_files)
                    return
                if len(sitemap.page_urls) >= settings.sitemap_max_page_urls:
                    logger.info("Stopping sitemap collection because sitemap URL limit is reached: limit=%s", settings.sitemap_max_page_urls)
                    return
                sitemap_url = sitemap_queue.pop(0)
                if not sitemap_url or sitemap_url in checked:
                    continue
                checked.add(sitemap_url)
                sitemap.checked = True
                document = await self._fetcher.fetch(
                    client,
                    sitemap_url,
                    render_html=False,
                    total_timeout_seconds=self._remaining_sitemap_fetch_budget_seconds(sitemap),
                )
                if document is None:
                    logger.info("Sitemap fetch returned no document: %s", sitemap_url)
                    continue
                sitemap_payload = document.body_bytes if document.body_bytes is not None else document.body
                parsed = parse_sitemap(sitemap_payload, self._allowed_host)
                allowed_page_urls = {url for url in parsed.page_urls if self._is_allowed_by_robots(url)}
                sitemap.page_urls.update(allowed_page_urls)
                if self._target.url and any(self._target.url_matches(url) for url in allowed_page_urls):
                    sitemap.found_target = True
                for nested_sitemap in parsed.nested_sitemaps:
                    if nested_sitemap not in checked and nested_sitemap not in sitemap_queue:
                        sitemap_queue.append(nested_sitemap)
                self._prioritize_sitemap_queue(sitemap_queue, checked=checked)
            sitemap.completed = True
        finally:
            sitemap.finished_at = perf_counter()
            logger.info(
                "Sitemap collection finished: checked=%s page_urls=%s nested_remaining=%s found_target=%s completed=%s",
                len(checked),
                len(sitemap.page_urls),
                len(sitemap_queue),
                sitemap.found_target,
                sitemap.completed,
            )

    def _remaining_sitemap_fetch_budget_seconds(self, sitemap: SitemapSnapshot) -> float:
        remaining_sitemap = self._remaining_sitemap_budget_seconds(sitemap)
        remaining_total = self._remaining_fetch_budget_seconds()
        if remaining_total is None:
            return remaining_sitemap
        return min(remaining_sitemap, remaining_total)

    def _remaining_sitemap_budget_seconds(self, sitemap: SitemapSnapshot) -> float:
        if sitemap.started_at is None:
            return max(settings.sitemap_time_budget_seconds, 0.0)
        elapsed = perf_counter() - sitemap.started_at
        return max(settings.sitemap_time_budget_seconds - elapsed, 0.0)

    def _sitemap_budget_exhausted(self, sitemap: SitemapSnapshot) -> bool:
        return self._remaining_sitemap_budget_seconds(sitemap) <= 0

    def _prioritize_sitemap_queue(self, sitemap_queue: list[str], *, checked: set[str]) -> None:
        sitemap_queue.sort(key=lambda url: self._sitemap_queue_sort_key(url, checked=checked))

    def _sitemap_queue_sort_key(self, url: str, *, checked: set[str]) -> tuple[int, str]:
        if url in checked:
            return (10_000, url)
        return (-self._score_sitemap_url(url), url)

    def _score_sitemap_url(self, url: str) -> int:
        score = self._candidate_branch_bonus(url, {self._target.url}) if self._target.url else 0
        sitemap_terms = set(extract_url_terms(url))
        if sitemap_terms:
            target_terms = (
                set(self._target.priority_terms)
                | set(self._target.signature_terms)
                | set(self._target.branch_terms)
                | set(self._target.core_branch_terms)
            )
            score += len(sitemap_terms & target_terms) * 12
        return score

    async def _fetch_node(self, client: FetchSession, node: CrawlNode) -> tuple[CrawlNode, ParsedPage | None]:
        if not self._is_allowed_by_robots(node.url):
            return node, None
        if self._is_html_403_branch_blocked(node.url):
            logger.debug("Skipping HTML URL from 403-blocked branch: %s", node.url)
            return node, None
        async with self._semaphore:
            document = await self._fetcher.fetch(
                client,
                node.url,
                total_timeout_seconds=self._remaining_fetch_budget_seconds(),
                failure_status_callback=self._record_html_fetch_failure_status,
            )
        if document is None:
            return node, None
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
            return node, None
        if not self._is_allowed_by_robots(normalized_final_url):
            return node, None
        page = parse_html(document.body, normalized_final_url, self._allowed_host)
        if normalized_final_url != node.url:
            node = CrawlNode(
                url=normalized_final_url,
                depth=node.depth,
                path=node.path[:-1] + [normalized_final_url],
                score=node.score,
                sitemap_boosted=node.sitemap_boosted,
            )
        return node, page

    def _record_html_fetch_failure_status(self, status_code: int, url: str) -> None:
        if status_code != 403:
            return
        branch_key = self._html_403_branch_key(url)
        if not branch_key:
            return
        count = self._html_403_branch_counts.get(branch_key, 0) + 1
        self._html_403_branch_counts[branch_key] = count
        if count >= settings.html_403_branch_skip_threshold:
            self._html_403_blocked_branches.add(branch_key)
            logger.info("HTML branch blocked after repeated 403 responses: branch=%s count=%s", branch_key, count)

    def _is_html_403_branch_blocked(self, url: str) -> bool:
        branch_key = self._html_403_branch_key(url)
        return bool(branch_key and branch_key in self._html_403_blocked_branches)

    def _html_403_branch_key(self, url: str) -> str | None:
        parsed = urlsplit(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts:
            return None
        target_parts = [part for part in urlsplit(self._target.url or "").path.split("/") if part]
        if target_parts and path_parts[0] == target_parts[0]:
            branch_parts = path_parts[: min(len(path_parts), max(3, len(target_parts) - 1))]
        else:
            branch_parts = path_parts[:1]
        if not branch_parts:
            return None
        return "/" + "/".join(branch_parts)
