from __future__ import annotations

import asyncio

from urllib.parse import urlsplit, urlunsplit

import httpx

from app.schemas import LinkingAnalyzeResponse
from app.services.internal_linking_models import SITEMAP_RECOMMENDATION_RANK_LIMIT
from app.services.link_placement import CrawledPageSnapshot
from app.services.llm_summary import AnalysisMessageContext
from app.services.parser import is_internal_url, normalize_url, parse_html
from app.settings import get_settings


settings = get_settings()


class InternalLinkingResponseMixin:
    async def _build_response(
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
        timings,
        client: httpx.AsyncClient,
        crawled_pages: dict[str, CrawledPageSnapshot],
        sitemap_page_urls: set[str],
        search_depth_limit: int,
    ) -> LinkingAnalyzeResponse:
        status = self._resolve_optimization_status(found=found, steps_to_target=steps_to_target)
        verified_candidate_depths: dict[str, int] = {}
        placement_recommendations = self._sanitize_placement_recommendations(
            self._placement_recommender.build_recommendations(
                found=found,
                steps_to_target=steps_to_target,
                path=path,
                crawled_pages=crawled_pages,
            )
        )

        if not placement_recommendations and sitemap_page_urls:
            sitemap_candidate_urls = self._rank_sitemap_candidate_urls(
                sitemap_page_urls=sitemap_page_urls,
                crawled_pages=crawled_pages,
            )
            sitemap_verified_depths = await self._verify_candidate_depths(
                client=client,
                candidate_urls=sitemap_candidate_urls,
                crawled_pages=crawled_pages,
            )
            self._merge_verified_depths(verified_candidate_depths, sitemap_verified_depths)
            pages_fetched += await self._populate_verified_candidate_snapshots(
                client=client,
                candidate_depths=sitemap_verified_depths,
                crawled_pages=crawled_pages,
            )
            placement_recommendations = self._sanitize_placement_recommendations(
                self._placement_recommender.build_recommendations(
                    found=found,
                    steps_to_target=steps_to_target,
                    path=path,
                    crawled_pages=crawled_pages,
                )
            )

        if not placement_recommendations and self._target.url:
            parent_verified_depths = await self._verify_candidate_depths(
                client=client,
                candidate_urls=self._candidate_parent_urls(),
                crawled_pages=crawled_pages,
            )
            self._merge_verified_depths(verified_candidate_depths, parent_verified_depths)
            pages_fetched += await self._populate_verified_candidate_snapshots(
                client=client,
                candidate_depths=parent_verified_depths,
                crawled_pages=crawled_pages,
            )
            placement_recommendations = self._sanitize_placement_recommendations(
                self._placement_recommender.build_recommendations(
                    found=found,
                    steps_to_target=steps_to_target,
                    path=path,
                    crawled_pages=crawled_pages,
                )
            )

        if not placement_recommendations and verified_candidate_depths:
            excluded_urls = set(path)
            placement_recommendations = self._sanitize_placement_recommendations(
                self._placement_recommender.build_url_only_recommendations(
                    sitemap_page_urls=set(verified_candidate_depths),
                    excluded_urls=excluded_urls,
                    verified_depths=verified_candidate_depths,
                )
            )

        if not placement_recommendations:
            placement_recommendations = self._sanitize_placement_recommendations(
                self._placement_recommender.build_soft_verified_recommendations(
                    crawled_pages=crawled_pages,
                    excluded_urls=set(path),
                )
            )

        if not placement_recommendations and sitemap_page_urls:
            relaxed_sitemap_candidate_urls = self._rank_sitemap_candidate_urls(
                sitemap_page_urls=sitemap_page_urls,
                crawled_pages=crawled_pages,
                relaxed=True,
            )
            relaxed_sitemap_verified_depths = await self._verify_candidate_depths(
                client=client,
                candidate_urls=relaxed_sitemap_candidate_urls,
                crawled_pages=crawled_pages,
            )
            self._merge_verified_depths(verified_candidate_depths, relaxed_sitemap_verified_depths)
            pages_fetched += await self._populate_verified_candidate_snapshots(
                client=client,
                candidate_depths=relaxed_sitemap_verified_depths,
                crawled_pages=crawled_pages,
            )
            placement_recommendations = self._sanitize_placement_recommendations(
                self._placement_recommender.build_soft_verified_recommendations(
                    crawled_pages=crawled_pages,
                    excluded_urls=set(path),
                )
            )

        if not placement_recommendations and verified_candidate_depths:
            excluded_urls = set(path)
            placement_recommendations = self._sanitize_placement_recommendations(
                self._placement_recommender.build_soft_url_only_recommendations(
                    sitemap_page_urls=set(verified_candidate_depths),
                    excluded_urls=excluded_urls,
                    verified_depths=verified_candidate_depths,
                )
            )

        placement_recommendations = await self._placement_reranker.rerank(
            target=self._target,
            recommendations=placement_recommendations,
        )
        generated_message = await self._message_generator.generate(
            AnalysisMessageContext(
                start_url=self._start_url,
                target_url=self._requested_target_url or self._target.url,
                target_title=self._target.title,
                found=found,
                optimization_status=status.value,
                steps_to_target=steps_to_target,
                good_depth_threshold=settings.good_depth_threshold,
                search_depth_limit=search_depth_limit,
                matched_by=matched_by,
                pages_fetched=pages_fetched,
                pages_discovered=pages_discovered,
                sitemap_checked=sitemap_checked,
                found_in_sitemap=found_in_sitemap,
                path=path,
                placement_recommendations=placement_recommendations,
            )
        )

        return LinkingAnalyzeResponse(
            start_url=self._start_url,
            target_url=self._requested_target_url or self._target.url,
            found=found,
            matched_by=matched_by,
            steps_to_target=steps_to_target,
            path=path,
            optimization_status=status,
            message=generated_message.text,
            message_source=generated_message.source,
            message_error=generated_message.error,
            pages_fetched=pages_fetched,
            pages_discovered=pages_discovered,
            sitemap_checked=sitemap_checked,
            found_in_sitemap=found_in_sitemap,
            strategy=strategy,
            timings=timings,
        )

    async def _populate_verified_candidate_snapshots(
        self,
        *,
        client: httpx.AsyncClient,
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
            await asyncio.gather(*tasks, return_exceptions=True)
        return fetched_count

    def _sanitize_placement_recommendations(self, recommendations: list) -> list:
        sanitized = []
        seen_urls: set[str] = set()
        for recommendation in recommendations:
            if self._target.url_matches(recommendation.source_url) or recommendation.source_url in seen_urls:
                continue
            if not self._placement_recommender._is_allowed_source_depth(recommendation.source_depth):
                continue
            seen_urls.add(recommendation.source_url)
            sanitized.append(recommendation)
        return sanitized

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
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _rank_sitemap_candidate_urls(
        self,
        *,
        sitemap_page_urls: set[str],
        crawled_pages: dict[str, CrawledPageSnapshot],
        relaxed: bool = False,
    ) -> list[str]:
        ranked: list[tuple[int, str]] = []
        for url in sitemap_page_urls:
            if url in crawled_pages or self._target.url_matches(url):
                continue
            score = self._placement_recommender.score_source_url_soft(url) if relaxed else self._placement_recommender.score_source_url(url)
            if score <= 0:
                continue
            ranked.append((score, url))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [url for _, url in ranked[:SITEMAP_RECOMMENDATION_RANK_LIMIT]]

    async def _fetch_recommendation_snapshot(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        depth: int | None = None,
    ) -> CrawledPageSnapshot | None:
        async with self._semaphore:
            document = await self._fetcher.fetch(client, url)
        if document is None:
            return None
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
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
