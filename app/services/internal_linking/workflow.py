from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from app.models import CrawlNode, CrawledPageSnapshot, CrawlDiagnosticsSnapshot, RobotsSnapshot, SitemapSnapshot
from app.schemas import LinkingAnalyzeResponse
from app.services.frontier import apply_sitemap_bonus, prioritize
from app.services.internal_linking.constants import LIVE_SITEMAP_STRATEGY
from app.services.parser import is_internal_url
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class InternalLinkingWorkflowMixin:
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
        self._auxiliary_pages_fetched = 0
        pages_fetched = 0
        discovered_urls: set[str] = {self._start_url}
        discovered_depths: dict[str, int] = {self._start_url: 0}
        discovered_paths: dict[str, list[str]] = {self._start_url: [self._start_url]}
        crawled_pages: dict[str, CrawledPageSnapshot] = {}
        sitemap_seeded_urls: set[str] = set()
        search_depth_limit = settings.crawl_max_depth
        target_verification_reserve_seconds = self._target_verification_budget_reserve_seconds()
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
                direct_parent_verification = await self._verify_direct_target_parent_bridge(
                    client=client,
                    crawled_pages=crawled_pages,
                    max_depth=search_depth_limit,
                )
                pages_fetched += direct_parent_verification.pages_fetched
                if direct_parent_verification.steps_to_target is not None:
                    direct_parent_path_url = (
                        direct_parent_verification.path[-1]
                        if direct_parent_verification.path
                        else self._target.url
                    )
                    return await self._build_response(
                        found=True,
                        matched_by=[self._target_url_match_reason(direct_parent_path_url or "")],
                        steps_to_target=direct_parent_verification.steps_to_target,
                        path=direct_parent_verification.path,
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

                current_level: list[CrawlNode] = []
                if self._is_allowed_by_robots(self._start_url):
                    current_level.append(CrawlNode(url=self._start_url, depth=0, path=[self._start_url]))
                else:
                    logger.warning("Start URL is blocked by robots.txt: %s", self._start_url)
                while current_level:
                    if self._budget_exhausted(reserve_seconds=target_verification_reserve_seconds):
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
                            if self._budget_exhausted(reserve_seconds=target_verification_reserve_seconds):
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
                                if not self._should_enqueue_link(link.url):
                                    continue
                                url_match_reason = self._target.url_match_reason(link.url)
                                if url_match_reason:
                                    self._cancel_pending(tasks)
                                    return await self._build_response(
                                        found=True,
                                        matched_by=[url_match_reason],
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
                                    next_depth = node.depth + 1
                                    existing_depth = discovered_depths.get(link.url)
                                    self._remember_depth(discovered_depths, link.url, next_depth)
                                    if existing_depth is None or next_depth < existing_depth:
                                        discovered_paths[link.url] = node.path + [link.url]
                                    continue
                                candidate = CrawlNode(
                                    url=link.url,
                                    depth=node.depth + 1,
                                    path=node.path + [link.url],
                                    score=self._score_discovered_link(link.url, link.anchor_text),
                                )
                                self._remember_depth(discovered_depths, link.url, candidate.depth)
                                discovered_paths[link.url] = candidate.path
                                existing = level_candidates.get(link.url)
                                if existing is None or candidate.score > existing.score:
                                    level_candidates[link.url] = candidate
                        next_level = list(level_candidates.values())
                        apply_sitemap_bonus(next_level, sitemap.page_urls)
                        if sitemap.page_urls and len(next_level) < settings.max_crawl_level_size:
                            next_level.extend(
                                self._build_sitemap_fallback_frontier(
                                    sitemap_page_urls=sitemap.page_urls,
                                    discovered_urls=discovered_urls | set(level_candidates),
                                    discovered_depths=discovered_depths,
                                    discovered_paths=discovered_paths,
                                    sitemap_seeded_urls=sitemap_seeded_urls,
                                )[: max(settings.max_crawl_level_size - len(next_level), 0)]
                            )
                        if not next_level:
                            await self._await_sitemap_for_recommendations(
                                sitemap_task,
                                sitemap,
                                pages_fetched=pages_fetched,
                            )
                            next_level = self._build_sitemap_fallback_frontier(
                                sitemap_page_urls=sitemap.page_urls,
                                discovered_urls=discovered_urls,
                                discovered_depths=discovered_depths,
                                discovered_paths=discovered_paths,
                                sitemap_seeded_urls=sitemap_seeded_urls,
                            )
                        discovered_urls.update(level_candidates.keys())
                        discovered_urls.update(node.url for node in next_level)
                        current_level = self._limit_nodes(
                            prioritize(next_level),
                            depth=next_level[0].depth if next_level else 0,
                        )
                    finally:
                        await self._gather_tasks_with_logging(tasks, context="crawl level fetch")

                parent_candidate_depths = await self._verify_candidate_depths(
                    client=client,
                    candidate_urls=self._candidate_parent_urls(),
                    crawled_pages=crawled_pages,
                    discovered_paths=discovered_paths,
                    reserve_seconds=self._recommendation_budget_reserve_seconds(),
                )
                self._merge_verified_depths(discovered_depths, parent_candidate_depths)

                target_parent_verification = await self._verify_target_parent_bridge(
                    client=client,
                    crawled_pages=crawled_pages,
                    discovered_depths=discovered_depths,
                    discovered_paths=discovered_paths,
                    max_depth=search_depth_limit,
                    reserve_seconds=self._recommendation_budget_reserve_seconds(),
                )
                pages_fetched += target_parent_verification.pages_fetched
                if target_parent_verification.steps_to_target is not None:
                    target_parent_path_url = target_parent_verification.path[-1] if target_parent_verification.path else self._target.url
                    return await self._build_response(
                        found=True,
                        matched_by=[self._target_url_match_reason(target_parent_path_url or "")],
                        steps_to_target=target_parent_verification.steps_to_target,
                        path=target_parent_verification.path,
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

                target_verification = await self._verify_target_path(
                    client=client,
                    crawled_pages=crawled_pages,
                    discovered_urls=discovered_urls,
                    max_depth=search_depth_limit,
                    reserve_seconds=self._recommendation_budget_reserve_seconds(),
                )
                pages_fetched += target_verification.pages_fetched
                if target_verification.steps_to_target is not None:
                    target_path_url = target_verification.path[-1] if target_verification.path else self._target.url
                    return await self._build_response(
                        found=True,
                        matched_by=[self._target_url_match_reason(target_path_url or "")],
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

    def _should_enqueue_link(self, url: str) -> bool:
        return (
            is_internal_url(url, self._allowed_host)
            and self._is_allowed_by_robots(url)
            and not self._is_html_403_branch_blocked(url)
        )

    def _build_sitemap_fallback_frontier(
        self,
        *,
        sitemap_page_urls: set[str],
        discovered_urls: set[str],
        discovered_depths: dict[str, int],
        discovered_paths: dict[str, list[str]],
        sitemap_seeded_urls: set[str],
    ) -> list[CrawlNode]:
        if not sitemap_page_urls:
            return []

        candidates: list[CrawlNode] = []
        for url in sitemap_page_urls:
            if url in discovered_urls or url in sitemap_seeded_urls:
                continue
            if self._target.url and self._target.url_matches(url):
                continue
            if not self._should_enqueue_link(url):
                continue
            estimated_depth = self._placement_recommender._estimated_structural_depth(url)
            max_sitemap_source_depth = max(settings.crawl_max_depth - 1, 0)
            min_sitemap_source_depth = min(2, max_sitemap_source_depth)
            sitemap_source_depth = min(
                max(estimated_depth if estimated_depth is not None else min_sitemap_source_depth, min_sitemap_source_depth),
                max_sitemap_source_depth,
            )

            node = CrawlNode(
                url=url,
                depth=sitemap_source_depth,
                path=[url],
                score=self._score_discovered_link(url, "") + settings.max_crawl_level_size,
                sitemap_boosted=True,
            )
            candidates.append(node)

        if not candidates:
            return []

        prioritized = prioritize(candidates)[: settings.max_crawl_level_size]
        for node in prioritized:
            sitemap_seeded_urls.add(node.url)
            self._remember_depth(discovered_depths, node.url, node.depth)
            discovered_paths[node.url] = node.path

        logger.info("Using sitemap fallback frontier: candidates=%s", len(prioritized))
        return prioritized
