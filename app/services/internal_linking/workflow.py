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
                                if not self._should_enqueue_link(link.url):
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

    def _should_enqueue_link(self, url: str) -> bool:
        return (
            is_internal_url(url, self._allowed_host)
            and self._is_allowed_by_robots(url)
            and not self._is_html_403_branch_blocked(url)
        )
