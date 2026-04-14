from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from app.schemas import AnalyzeTimings, OptimizationStatus
from app.services.internal_linking.constants import RECOMMENDATION_PHASE_MAX_SECONDS, RECOMMENDATION_PHASE_RESERVE_RATIO
from app.models import SitemapSnapshot
from app.settings import get_settings


settings = get_settings()
FETCH_BUDGET_SAFETY_MARGIN_SECONDS = 0.5
logger = logging.getLogger(__name__)


class InternalLinkingRuntimeMixin:
    def _budget_exhausted(self, *, reserve_seconds: float = 0.0) -> bool:
        remaining = self._remaining_budget_seconds()
        exhausted = remaining is not None and remaining <= reserve_seconds
        if exhausted and getattr(self, "_crawl_diagnostics", None) is not None:
            self._crawl_diagnostics.budget_exhausted = True
        return exhausted

    def _recommendation_budget_reserve_seconds(self) -> float:
        total_budget = max(settings.analyze_time_budget_seconds, 0.0)
        request_based_budget = max(settings.request_timeout_seconds, 0.0) * 0.75
        return min(
            RECOMMENDATION_PHASE_MAX_SECONDS,
            total_budget * RECOMMENDATION_PHASE_RESERVE_RATIO,
            request_based_budget,
        )

    def _remaining_budget_seconds(self) -> float | None:
        if self._deadline_started_at is None:
            return None
        return settings.analyze_time_budget_seconds - (perf_counter() - self._deadline_started_at)

    def _remaining_fetch_budget_seconds(self) -> float | None:
        remaining = self._remaining_budget_seconds()
        if remaining is None:
            return None
        return max(remaining - FETCH_BUDGET_SAFETY_MARGIN_SECONDS, 0.0)

    def _limit_nodes(self, nodes: list, *, depth: int) -> list:
        if len(nodes) <= settings.max_crawl_level_size:
            return nodes
        diagnostics = getattr(self, "_crawl_diagnostics", None)
        if diagnostics is not None:
            diagnostics.level_truncated = True
            diagnostics.truncated_levels += 1
            diagnostics.truncated_nodes += len(nodes) - settings.max_crawl_level_size
        return nodes[: settings.max_crawl_level_size]

    @staticmethod
    def _remember_crawled_page(crawled_pages: dict, snapshot) -> None:
        existing = crawled_pages.get(snapshot.url)
        if existing is None:
            crawled_pages[snapshot.url] = snapshot
            return
        if existing.depth is None and snapshot.depth is not None:
            crawled_pages[snapshot.url] = snapshot
            return
        if existing.depth is not None and snapshot.depth is not None and snapshot.depth < existing.depth:
            crawled_pages[snapshot.url] = snapshot

    @staticmethod
    def _merge_verified_depths(target_depths: dict[str, int], new_depths: dict[str, int]) -> None:
        for url, depth in new_depths.items():
            existing = target_depths.get(url)
            if existing is None or depth < existing:
                target_depths[url] = depth

    @staticmethod
    def _remember_depth(target_depths: dict[str, int], url: str, depth: int) -> None:
        existing = target_depths.get(url)
        if existing is None or depth < existing:
            target_depths[url] = depth

    @staticmethod
    def _resolve_optimization_status(*, found: bool, steps_to_target: int | None) -> OptimizationStatus:
        if found and steps_to_target is not None and steps_to_target <= settings.good_depth_threshold:
            return OptimizationStatus.GOOD
        if not found:
            return OptimizationStatus.NOT_FOUND
        return OptimizationStatus.BAD

    @staticmethod
    def _cancel_pending(tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()

    @staticmethod
    async def _gather_tasks_with_logging(tasks: list[asyncio.Task], *, context: str) -> None:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                logger.error(
                    "Background task failed during %s.",
                    context,
                    exc_info=(type(result), result, result.__traceback__),
                )

    def _build_timings(
        self,
        *,
        started_at: float,
        finished_at: float,
        found: bool,
        sitemap: SitemapSnapshot,
    ) -> AnalyzeTimings:
        sitemap_elapsed_ms: float | None = None
        if sitemap.started_at is not None:
            sitemap_end = sitemap.finished_at if sitemap.finished_at is not None else finished_at
            sitemap_elapsed_ms = self._to_milliseconds(sitemap_end - sitemap.started_at)
        total_ms = self._to_milliseconds(finished_at - started_at)
        return AnalyzeTimings(
            total_ms=total_ms,
            match_ms=total_ms if found else None,
            sitemap_elapsed_ms=sitemap_elapsed_ms,
            sitemap_completed=sitemap.completed,
        )

    @staticmethod
    def _to_milliseconds(seconds: float) -> float:
        return round(seconds * 1000, 3)
