from __future__ import annotations

import asyncio
from time import perf_counter

from app.schemas import AnalyzeTimings, OptimizationStatus
from app.services.internal_linking_models import (
    RECOMMENDATION_PHASE_MAX_SECONDS,
    RECOMMENDATION_PHASE_RESERVE_RATIO,
    SitemapSnapshot,
)
from app.settings import get_settings


settings = get_settings()
FETCH_BUDGET_SAFETY_MARGIN_SECONDS = 0.5


class InternalLinkingRuntimeMixin:
    def _budget_exhausted(self, *, reserve_seconds: float = 0.0) -> bool:
        remaining = self._remaining_budget_seconds()
        return remaining is not None and remaining <= reserve_seconds

    def _recommendation_budget_reserve_seconds(self) -> float:
        total_budget = max(settings.analyze_time_budget_seconds, 0.0)
        request_based_budget = max(self._request.timeout_seconds, 0.0) * 0.75
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

    @staticmethod
    def _limit_nodes(nodes: list) -> list:
        if len(nodes) <= settings.max_crawl_level_size:
            return nodes
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
        return OptimizationStatus.BAD

    @staticmethod
    def _cancel_pending(tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()

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
