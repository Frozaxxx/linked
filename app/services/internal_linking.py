from __future__ import annotations

import asyncio
from time import perf_counter

import httpx

from app.schemas import LinkingAnalyzeRequest, LinkingAnalyzeResponse
from app.services.fetcher import AsyncFetcher, FetchSession
from app.services.frontier import CrawlNode, apply_sitemap_bonus, prioritize, score_link
from app.services.internal_linking_models import (
    LIVE_SITEMAP_STRATEGY,
    SITEMAP_WAIT_TIMEOUT_SECONDS,
    SitemapSnapshot,
)
from app.services.internal_linking_response import InternalLinkingResponseMixin
from app.services.internal_linking_runtime import InternalLinkingRuntimeMixin
from app.services.internal_linking_verification import InternalLinkingVerificationMixin
from app.services.link_placement import CrawledPageSnapshot, LinkPlacementRecommender
from app.services.llm_reranker import PlacementRecommendationReranker
from app.services.llm_summary import LinkingAnalysisMessageGenerator
from app.services.matcher import SearchTarget
from app.services.parser import (
    ParsedPage,
    canonical_host,
    get_site_root,
    is_internal_url,
    normalize_url,
    parse_html,
    parse_sitemap,
)
from app.settings import get_settings


settings = get_settings()


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
        self._request = request
        self._start_url = normalize_url(str(request.start_url))
        self._allowed_host = canonical_host(httpx.URL(self._start_url).host if self._start_url else None)
        self._requested_target_url = normalize_url(str(request.target_url)) if request.target_url else None
        self._requested_target_title = request.target_title
        self._requested_target_text = request.target_text
        self._target = SearchTarget(
            url=self._requested_target_url,
            title=self._requested_target_title,
            text=self._requested_target_text,
        )
        self._fetcher = AsyncFetcher(
            timeout_seconds=request.timeout_seconds,
            retry_count=request.retry_count,
            transport=transport,
        )
        self._placement_recommender = self._build_placement_recommender()
        self._placement_reranker = PlacementRecommendationReranker()
        self._message_generator = LinkingAnalysisMessageGenerator()
        self._semaphore = asyncio.Semaphore(settings.crawl_concurrency)
        self._deadline_started_at: float | None = None

    def _build_placement_recommender(self) -> LinkPlacementRecommender:
        return LinkPlacementRecommender(
            target=self._target,
            start_url=self._start_url or "",
            good_depth_threshold=settings.good_depth_threshold,
        )

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

    async def _resolve_target_metadata(self, client: FetchSession) -> int:
        if not self._requested_target_url or not is_internal_url(self._requested_target_url, self._allowed_host):
            return 0
        document = await self._fetcher.fetch(
            client,
            self._requested_target_url,
            total_timeout_seconds=self._remaining_fetch_budget_seconds(),
        )
        if document is None:
            return 0
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
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
            raise ValueError("Не удалось нормализовать start_url.")

        started_at = perf_counter()
        self._deadline_started_at = started_at
        pages_fetched = 0
        discovered_urls: set[str] = {self._start_url}
        discovered_depths: dict[str, int] = {self._start_url: 0}
        crawled_pages: dict[str, CrawledPageSnapshot] = {}
        search_depth_limit = settings.good_depth_threshold

        async with self._fetcher.create_client() as client:
            pages_fetched += await self._resolve_target_metadata(client)
            sitemap = SitemapSnapshot(started_at=perf_counter())
            sitemap_task = asyncio.create_task(self._collect_sitemap_snapshot(client, sitemap))
            try:
                current_level: list[CrawlNode] = [CrawlNode(url=self._start_url, depth=0, path=[self._start_url])]
                while current_level:
                    if self._budget_exhausted():
                        break
                    level_candidates: dict[str, CrawlNode] = {}
                    tasks = [asyncio.create_task(self._fetch_node(client, node)) for node in self._limit_nodes(current_level)]
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
                                continue
                            for link in page.links:
                                if not is_internal_url(link.url, self._allowed_host):
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
                                    score=score_link(link.url, link.anchor_text, self._target.priority_terms),
                                )
                                self._remember_depth(discovered_depths, link.url, candidate.depth)
                                existing = level_candidates.get(link.url)
                                if existing is None or candidate.score > existing.score:
                                    level_candidates[link.url] = candidate
                        next_level = list(level_candidates.values())
                        apply_sitemap_bonus(next_level, sitemap.page_urls)
                        discovered_urls.update(level_candidates.keys())
                        current_level = self._limit_nodes(prioritize(next_level))
                    finally:
                        await asyncio.gather(*tasks, return_exceptions=True)

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
                await asyncio.gather(sitemap_task, return_exceptions=True)

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
            timeout = max(timeout, self._request.timeout_seconds)
        remaining_budget = self._remaining_budget_seconds()
        if remaining_budget is not None:
            usable_budget = remaining_budget - reserve_seconds
            if usable_budget <= 0:
                return
            timeout = min(timeout, usable_budget)
        try:
            await asyncio.wait_for(asyncio.shield(sitemap_task), timeout=timeout)
        except asyncio.TimeoutError:
            return

    async def _collect_sitemap_snapshot(self, client: FetchSession, sitemap: SitemapSnapshot) -> None:
        sitemap_queue = [normalize_url("sitemap.xml", get_site_root(self._start_url), allow_ignored_extensions=True)]
        checked: set[str] = set()
        try:
            while sitemap_queue:
                if self._budget_exhausted():
                    return
                sitemap_url = sitemap_queue.pop(0)
                if not sitemap_url or sitemap_url in checked:
                    continue
                checked.add(sitemap_url)
                sitemap.checked = True
                async with self._semaphore:
                    document = await self._fetcher.fetch(
                        client,
                        sitemap_url,
                        render_html=False,
                        total_timeout_seconds=self._remaining_fetch_budget_seconds(),
                    )
                if document is None:
                    continue
                parsed = parse_sitemap(document.body, self._allowed_host)
                sitemap.page_urls.update(parsed.page_urls)
                if self._target.url and any(self._target.url_matches(url) for url in parsed.page_urls):
                    sitemap.found_target = True
                for nested_sitemap in parsed.nested_sitemaps:
                    if nested_sitemap not in checked:
                        sitemap_queue.append(nested_sitemap)
            sitemap.completed = True
        finally:
            sitemap.finished_at = perf_counter()

    async def _fetch_node(self, client: FetchSession, node: CrawlNode) -> tuple[CrawlNode, ParsedPage | None]:
        async with self._semaphore:
            document = await self._fetcher.fetch(
                client,
                node.url,
                total_timeout_seconds=self._remaining_fetch_budget_seconds(),
            )
        if document is None:
            return node, None
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
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
