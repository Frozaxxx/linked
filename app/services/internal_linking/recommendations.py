from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

from app.models import CrawledPageSnapshot
from app.services.fetcher import FetchSession
from app.services.internal_linking.constants import SITEMAP_RECOMMENDATION_RANK_LIMIT
from app.services.link_placement_models import MAX_RECOMMENDATIONS
from app.services.parser import is_internal_url, normalize_url, parse_html


class InternalLinkingRecommendationMixin:
    async def _populate_verified_candidate_snapshots(
        self,
        *,
        client: FetchSession,
        candidate_depths: dict[str, int],
        crawled_pages: dict[str, CrawledPageSnapshot],
    ) -> int:
        if self._budget_exhausted():
            return 0
        pending: list[tuple[str, int]] = []
        for url, depth in candidate_depths.items():
            existing = crawled_pages.get(url)
            if existing is not None and existing.depth is not None and existing.depth <= depth:
                continue
            pending.append((url, depth))
        if not pending:
            return 0

        tasks = [
            asyncio.create_task(self._fetch_recommendation_snapshot(client, url, depth=depth))
            for url, depth in pending
        ]
        fetched_count = 0
        try:
            for task in asyncio.as_completed(tasks):
                snapshot = await task
                if self._budget_exhausted():
                    self._cancel_pending(tasks)
                    break
                if snapshot is None:
                    continue
                fetched_count += 1
                self._remember_crawled_page(crawled_pages, snapshot)
        finally:
            await self._gather_tasks_with_logging(tasks, context="recommendation snapshot fetch")
        return fetched_count

    def _sanitize_placement_recommendations(self, recommendations: list) -> list:
        sanitized = []
        seen_urls: set[str] = set()
        for recommendation in recommendations:
            if self._target.url_matches(recommendation.source_url) or recommendation.source_url in seen_urls:
                continue
            if not self._recommendation_url_allowed(recommendation.source_url):
                continue
            if not self._placement_recommender._is_allowed_source_depth(recommendation.source_depth):
                continue
            seen_urls.add(recommendation.source_url)
            sanitized.append(recommendation)
        return sanitized

    def _extend_placement_recommendations(self, current: list, candidates: list) -> list:
        if not candidates:
            return current
        return self._sanitize_placement_recommendations([*current, *candidates])[:MAX_RECOMMENDATIONS]

    @staticmethod
    def _needs_more_placement_recommendations(recommendations: list) -> bool:
        return len(recommendations) < MAX_RECOMMENDATIONS

    def _build_depth_based_recommendations(
        self,
        *,
        candidate_depths: dict[str, int],
        path: list[str],
    ) -> list:
        if not candidate_depths:
            return []
        excluded_urls = set(path)
        recommendations = self._placement_recommender.build_soft_url_only_recommendations(
            sitemap_page_urls=set(candidate_depths),
            excluded_urls=excluded_urls,
            verified_depths=candidate_depths,
        )
        return self._sanitize_placement_recommendations(recommendations)

    def _build_forced_recommendations(
        self,
        *,
        crawled_pages: dict[str, CrawledPageSnapshot],
        discovered_depths: dict[str, int],
        verified_candidate_depths: dict[str, int],
        sitemap_page_urls: set[str],
        path: list[str],
    ) -> list:
        excluded_urls = set(path)
        merged_depths = dict(discovered_depths)
        self._merge_verified_depths(merged_depths, verified_candidate_depths)
        candidates = self._placement_recommender.build_soft_verified_recommendations(
            crawled_pages=crawled_pages,
            excluded_urls=excluded_urls,
        )
        if merged_depths:
            candidates.extend(
                self._placement_recommender.build_soft_url_only_recommendations(
                    sitemap_page_urls=set(merged_depths),
                    excluded_urls=excluded_urls,
                    verified_depths=merged_depths,
                )
            )
        structural_pool = set(sitemap_page_urls) | set(merged_depths) | set(self._candidate_parent_urls())
        if structural_pool:
            candidates.extend(
                self._placement_recommender.build_structural_recommendations(
                    sitemap_page_urls=structural_pool,
                    excluded_urls=excluded_urls,
                )
            )
        return self._sanitize_placement_recommendations(candidates)

    def _candidate_parent_urls(self) -> list[str]:
        if not self._target.url:
            return []
        parsed = urlsplit(self._target.url)
        parts = [part for part in parsed.path.split("/") if part]
        candidates: list[str] = []
        for end in range(len(parts) - 1, 0, -1):
            parent_path = "/" + "/".join(parts[:end])
            candidate = urlunsplit((parsed.scheme, parsed.netloc, parent_path, "", ""))
            normalized = normalize_url(candidate)
            if normalized and self._recommendation_url_allowed(normalized) and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _recommendation_url_allowed(self, url: str) -> bool:
        checker = getattr(self, "_is_allowed_by_robots", None)
        if checker is None:
            return True
        return checker(url)

    def _rank_sitemap_candidate_urls(
        self,
        *,
        sitemap_page_urls: set[str],
        crawled_pages: dict[str, CrawledPageSnapshot],
    ) -> list[str]:
        ranked: list[tuple[int, str]] = []
        for url in sitemap_page_urls:
            if url in crawled_pages or self._target.url_matches(url):
                continue
            score = self._placement_recommender.score_source_url_soft(url)
            if score <= 0:
                continue
            ranked.append((score, url))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [url for _, url in ranked[:SITEMAP_RECOMMENDATION_RANK_LIMIT]]

    async def _fetch_recommendation_snapshot(
        self,
        client: FetchSession,
        url: str,
        *,
        depth: int | None = None,
    ) -> CrawledPageSnapshot | None:
        if not self._is_allowed_by_robots(url):
            return None
        async with self._semaphore:
            document = await self._fetcher.fetch(
                client,
                url,
                total_timeout_seconds=self._remaining_fetch_budget_seconds(),
            )
        if document is None:
            return None
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
            return None
        if not self._is_allowed_by_robots(normalized_final_url):
            return None
        if self._target.url and self._target.url_matches(normalized_final_url):
            return None
        page = parse_html(document.body, normalized_final_url, self._allowed_host)
        return self._placement_recommender.build_snapshot(
            url=page.url,
            title=page.title,
            h1=page.h1,
            depth=depth,
            text=page.text,
            is_indexable=page.is_indexable,
            links_to_target=bool(self._target.url and any(self._target.url_matches(link.url) for link in page.links)),
        )
