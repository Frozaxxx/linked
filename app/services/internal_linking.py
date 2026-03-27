from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import perf_counter

import httpx

from app.schemas import AnalyzeTimings, LinkingAnalyzeRequest, LinkingAnalyzeResponse, OptimizationStatus
from app.services.fetcher import AsyncFetcher
from app.services.frontier import CrawlNode, apply_sitemap_bonus, prioritize, score_link
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

LIVE_SITEMAP_STRATEGY = "bfs + live sitemap"


@dataclass(slots=True)
class SitemapSnapshot:
    checked: bool = False
    page_urls: set[str] = field(default_factory=set)
    found_target: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    completed: bool = False


class InternalLinkingAnalyzer:
    def __init__(
        self,
        request: LinkingAnalyzeRequest,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._request = request
        self._start_url = normalize_url(str(request.start_url))
        self._allowed_host = canonical_host(httpx.URL(self._start_url).host if self._start_url else None)
        self._target = SearchTarget(
            url=normalize_url(str(request.target_url)) if request.target_url else None,
            title=request.target_title,
            text=request.target_text,
        )
        self._fetcher = AsyncFetcher(
            timeout_seconds=request.timeout_seconds,
            retry_count=request.retry_count,
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(settings.crawl_concurrency)

    async def analyze(self) -> LinkingAnalyzeResponse:
        if not self._start_url:
            raise ValueError("Не удалось нормализовать start_url.")

        started_at = perf_counter()
        pages_fetched = 0
        discovered_urls: set[str] = {self._start_url}
        search_depth_limit = settings.good_depth_threshold

        async with self._fetcher.create_client() as client:
            sitemap = SitemapSnapshot(started_at=perf_counter())
            sitemap_task = asyncio.create_task(self._collect_sitemap_snapshot(client, sitemap))

            try:
                current_level: list[CrawlNode] = [CrawlNode(url=self._start_url, depth=0, path=[self._start_url])]

                while current_level:
                    level_candidates: dict[str, CrawlNode] = {}
                    tasks = [asyncio.create_task(self._fetch_node(client, node)) for node in current_level]

                    try:
                        for task in asyncio.as_completed(tasks):
                            node, page = await task
                            if page is None:
                                continue

                            pages_fetched += 1
                            matched_by = self._target.page_matches(page.url, page.title, page.text)
                            if matched_by:
                                self._cancel_pending(tasks)
                                return self._build_response(
                                    found=True,
                                    matched_by=matched_by,
                                    steps_to_target=node.depth,
                                    path=node.path,
                                    pages_fetched=pages_fetched,
                                    pages_discovered=len(discovered_urls),
                                    sitemap_checked=sitemap.checked,
                                    found_in_sitemap=sitemap.found_target,
                                    strategy=LIVE_SITEMAP_STRATEGY,
                                    timings=self._build_timings(
                                        started_at=started_at,
                                        finished_at=perf_counter(),
                                        found=True,
                                        sitemap=sitemap,
                                    ),
                                    search_depth_limit=search_depth_limit,
                                )

                            if node.depth >= search_depth_limit:
                                continue

                            for link in page.links:
                                if not is_internal_url(link.url, self._allowed_host):
                                    continue

                                if self._target.url_matches(link.url):
                                    self._cancel_pending(tasks)
                                    return self._build_response(
                                        found=True,
                                        matched_by=["url"],
                                        steps_to_target=node.depth + 1,
                                        path=node.path + [link.url],
                                        pages_fetched=pages_fetched,
                                        pages_discovered=len(discovered_urls),
                                        sitemap_checked=sitemap.checked,
                                        found_in_sitemap=sitemap.found_target,
                                        strategy=LIVE_SITEMAP_STRATEGY,
                                        timings=self._build_timings(
                                            started_at=started_at,
                                            finished_at=perf_counter(),
                                            found=True,
                                            sitemap=sitemap,
                                        ),
                                        search_depth_limit=search_depth_limit,
                                    )

                                if link.url in discovered_urls:
                                    continue

                                candidate = CrawlNode(
                                    url=link.url,
                                    depth=node.depth + 1,
                                    path=node.path + [link.url],
                                    score=score_link(link.url, link.anchor_text, self._target.priority_terms),
                                )
                                existing = level_candidates.get(link.url)
                                if existing is None or candidate.score > existing.score:
                                    level_candidates[link.url] = candidate

                        next_level = list(level_candidates.values())
                        apply_sitemap_bonus(next_level, sitemap.page_urls)
                        discovered_urls.update(level_candidates.keys())
                        current_level = prioritize(next_level)
                    finally:
                        await asyncio.gather(*tasks, return_exceptions=True)

                return self._build_response(
                    found=False,
                    matched_by=[],
                    steps_to_target=None,
                    path=[],
                    pages_fetched=pages_fetched,
                    pages_discovered=len(discovered_urls),
                    sitemap_checked=sitemap.checked,
                    found_in_sitemap=sitemap.found_target,
                    strategy=LIVE_SITEMAP_STRATEGY,
                    timings=self._build_timings(
                        started_at=started_at,
                        finished_at=perf_counter(),
                        found=False,
                        sitemap=sitemap,
                    ),
                    search_depth_limit=search_depth_limit,
                )
            finally:
                if not sitemap_task.done():
                    sitemap_task.cancel()
                await asyncio.gather(sitemap_task, return_exceptions=True)

    async def _collect_sitemap_snapshot(self, client: httpx.AsyncClient, sitemap: SitemapSnapshot) -> None:
        sitemap_queue = [
            normalize_url(
                "sitemap.xml",
                get_site_root(self._start_url),
                allow_ignored_extensions=True,
            )
        ]
        checked: set[str] = set()

        try:
            while sitemap_queue:
                sitemap_url = sitemap_queue.pop(0)
                if not sitemap_url or sitemap_url in checked:
                    continue
                checked.add(sitemap_url)
                sitemap.checked = True

                async with self._semaphore:
                    document = await self._fetcher.fetch(client, sitemap_url)
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

    async def _fetch_node(self, client: httpx.AsyncClient, node: CrawlNode) -> tuple[CrawlNode, ParsedPage | None]:
        async with self._semaphore:
            document = await self._fetcher.fetch(client, node.url)
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

    def _build_response(
        self,
        *,
        found: bool,
        matched_by: list[str],
        steps_to_target: int | None,
        path: list[str],
        pages_fetched: int,
        pages_discovered: int,
        sitemap_checked: bool,
        found_in_sitemap: bool,
        strategy: str,
        timings: AnalyzeTimings,
        search_depth_limit: int,
    ) -> LinkingAnalyzeResponse:
        if found and steps_to_target is not None and steps_to_target <= settings.good_depth_threshold:
            status = OptimizationStatus.GOOD
            message = (
                f"Хорошая перелинковка: целевая страница найдена за {steps_to_target} шаг(а/ов), "
                f"порог {settings.good_depth_threshold}."
            )
        elif found:
            status = OptimizationStatus.BAD
            message = (
                f"Плохое SEO: целевая страница найдена за {steps_to_target} шаг(а/ов), "
                f"что больше порога {settings.good_depth_threshold}."
            )
        elif search_depth_limit == settings.good_depth_threshold:
            status = OptimizationStatus.BAD
            message = (
                f"Плохое SEO: целевая страница не найдена за {search_depth_limit} шаг(а/ов). "
                "Поиск остановлен."
            )
        else:
            status = OptimizationStatus.NOT_FOUND
            message = (
                f"Целевая страница не найдена до глубины {search_depth_limit}. "
                "Проверьте URL."
            )

        return LinkingAnalyzeResponse(
            start_url=self._start_url,
            target_url=self._target.url,
            found=found,
            matched_by=matched_by,
            steps_to_target=steps_to_target,
            path=path,
            optimization_status=status,
            message=message,
            pages_fetched=pages_fetched,
            pages_discovered=pages_discovered,
            sitemap_checked=sitemap_checked,
            found_in_sitemap=found_in_sitemap,
            strategy=strategy,
            timings=timings,
        )

    @staticmethod
    def _cancel_pending(tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()

    @staticmethod
    def _build_timings(
        *,
        started_at: float,
        finished_at: float,
        found: bool,
        sitemap: SitemapSnapshot,
    ) -> AnalyzeTimings:
        sitemap_elapsed_ms: float | None = None
        if sitemap.started_at is not None:
            sitemap_end = sitemap.finished_at if sitemap.finished_at is not None else finished_at
            sitemap_elapsed_ms = InternalLinkingAnalyzer._to_milliseconds(sitemap_end - sitemap.started_at)

        total_ms = InternalLinkingAnalyzer._to_milliseconds(finished_at - started_at)
        return AnalyzeTimings(
            total_ms=total_ms,
            match_ms=total_ms if found else None,
            sitemap_elapsed_ms=sitemap_elapsed_ms,
            sitemap_completed=sitemap.completed,
        )

    @staticmethod
    def _to_milliseconds(seconds: float) -> float:
        return round(seconds * 1000, 3)
