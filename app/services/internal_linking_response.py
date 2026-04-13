from __future__ import annotations

import asyncio
import logging

from urllib.parse import urlsplit, urlunsplit

from app.schemas import CrawlDiagnostics, FetchStats, LinkingAnalyzeResponse
from app.services.fetcher import FetchSession
from app.services.internal_linking_models import SITEMAP_RECOMMENDATION_RANK_LIMIT
from app.services.link_placement import CrawledPageSnapshot
from app.services.link_placement_models import MAX_RECOMMENDATIONS
from app.services.llm_summary import AnalysisMessageContext
from app.services.parser import is_internal_url, normalize_url, parse_html
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


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
        client: FetchSession,
        crawled_pages: dict[str, CrawledPageSnapshot],
        discovered_depths: dict[str, int],
        sitemap_page_urls: set[str],
        search_depth_limit: int,
    ) -> LinkingAnalyzeResponse:
        status = self._resolve_optimization_status(found=found, steps_to_target=steps_to_target)
        verified_candidate_depths: dict[str, int] = {}
        placement_recommendations = self._extend_placement_recommendations(
            [],
            self._placement_recommender.build_soft_verified_recommendations(
                crawled_pages=crawled_pages,
                excluded_urls=set(path),
            ),
        )

        if self._needs_more_placement_recommendations(placement_recommendations) and discovered_depths:
            placement_recommendations = self._extend_placement_recommendations(
                placement_recommendations,
                self._build_depth_based_recommendations(
                    candidate_depths=discovered_depths,
                    path=path,
                ),
            )

        if self._needs_more_placement_recommendations(placement_recommendations) and sitemap_page_urls:
            placement_recommendations = self._extend_placement_recommendations(
                placement_recommendations,
                self._placement_recommender.build_structural_recommendations(
                    sitemap_page_urls=sitemap_page_urls,
                    excluded_urls=set(path),
                ),
            )

        if self._needs_more_placement_recommendations(placement_recommendations) and self._target.url:
            placement_recommendations = self._extend_placement_recommendations(
                placement_recommendations,
                self._placement_recommender.build_structural_recommendations(
                    sitemap_page_urls=set(self._candidate_parent_urls()),
                    excluded_urls=set(path),
                ),
            )

        if self._needs_more_placement_recommendations(placement_recommendations) and sitemap_page_urls:
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
            placement_recommendations = self._extend_placement_recommendations(
                placement_recommendations,
                self._placement_recommender.build_soft_verified_recommendations(
                    crawled_pages=crawled_pages,
                    excluded_urls=set(path),
                ),
            )

        if self._needs_more_placement_recommendations(placement_recommendations) and self._target.url:
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
            placement_recommendations = self._extend_placement_recommendations(
                placement_recommendations,
                self._placement_recommender.build_soft_verified_recommendations(
                    crawled_pages=crawled_pages,
                    excluded_urls=set(path),
                ),
            )

        if self._needs_more_placement_recommendations(placement_recommendations) and verified_candidate_depths:
            excluded_urls = set(path)
            placement_recommendations = self._extend_placement_recommendations(
                placement_recommendations,
                self._placement_recommender.build_soft_url_only_recommendations(
                    sitemap_page_urls=set(verified_candidate_depths),
                    excluded_urls=excluded_urls,
                    verified_depths=verified_candidate_depths,
                ),
            )

        if self._needs_more_placement_recommendations(placement_recommendations):
            placement_recommendations = self._extend_placement_recommendations(
                placement_recommendations,
                self._build_forced_recommendations(
                    crawled_pages=crawled_pages,
                    discovered_depths=discovered_depths,
                    verified_candidate_depths=verified_candidate_depths,
                    sitemap_page_urls=sitemap_page_urls,
                    path=path,
                ),
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
                html_fetch_mode=client.html_fetch_mode,
                sitemap_fetch_mode=client.sitemap_fetch_mode,
                crawl_max_depth=self._crawl_diagnostics.crawl_max_depth,
                budget_exhausted=self._crawl_diagnostics.budget_exhausted,
                depth_cutoff=self._crawl_diagnostics.depth_cutoff,
                level_truncated=self._crawl_diagnostics.level_truncated,
                truncated_levels=self._crawl_diagnostics.truncated_levels,
                truncated_nodes=self._crawl_diagnostics.truncated_nodes,
                path=path,
                placement_recommendations=placement_recommendations,
            )
        )
        if (
            self._crawl_diagnostics.budget_exhausted
            or self._crawl_diagnostics.depth_cutoff
            or self._crawl_diagnostics.level_truncated
        ):
            logger.warning(
                "Analysis completed with crawl limitations: start_url=%s target_url=%s budget_exhausted=%s depth_cutoff=%s level_truncated=%s truncated_levels=%s truncated_nodes=%s",
                self._start_url,
                self._requested_target_url or self._target.url,
                self._crawl_diagnostics.budget_exhausted,
                self._crawl_diagnostics.depth_cutoff,
                self._crawl_diagnostics.level_truncated,
                self._crawl_diagnostics.truncated_levels,
                self._crawl_diagnostics.truncated_nodes,
            )
        logger.info(
            "Analysis response built: start_url=%s target_url=%s found=%s status=%s steps=%s matched_by=%s pages_fetched=%s pages_discovered=%s recommendations=%s sitemap_checked=%s found_in_sitemap=%s",
            self._start_url,
            self._requested_target_url or self._target.url,
            found,
            status.value,
            steps_to_target,
            ",".join(matched_by) if matched_by else "-",
            pages_fetched,
            pages_discovered,
            len(placement_recommendations),
            sitemap_checked,
            found_in_sitemap,
        )
        logger.info(
            "Fetch stats: start_url=%s playwright_available=%s html_playwright_attempts=%s html_playwright_successes=%s html_playwright_failures=%s html_playwright_timeout_failures=%s html_playwright_http_status_failures=%s html_playwright_no_response_failures=%s html_playwright_other_failures=%s html_playwright_failure_status_codes=%s html_http_attempts=%s html_http_successes=%s html_http_failures=%s html_http_fallback_successes=%s html_http_fallback_failures=%s sitemap_http_attempts=%s sitemap_http_successes=%s sitemap_http_failures=%s",
            self._start_url,
            client.fetch_stats.playwright_session_available,
            client.fetch_stats.html_playwright_attempts,
            client.fetch_stats.html_playwright_successes,
            client.fetch_stats.html_playwright_failures,
            client.fetch_stats.html_playwright_timeout_failures,
            client.fetch_stats.html_playwright_http_status_failures,
            client.fetch_stats.html_playwright_no_response_failures,
            client.fetch_stats.html_playwright_other_failures,
            client.fetch_stats.html_playwright_failure_status_codes,
            client.fetch_stats.html_http_attempts,
            client.fetch_stats.html_http_successes,
            client.fetch_stats.html_http_failures,
            client.fetch_stats.html_http_fallback_successes,
            client.fetch_stats.html_http_fallback_failures,
            client.fetch_stats.sitemap_http_attempts,
            client.fetch_stats.sitemap_http_successes,
            client.fetch_stats.sitemap_http_failures,
        )
        if not found and not placement_recommendations:
            logger.warning(
                "Analysis produced no placement recommendations: start_url=%s target_url=%s",
                self._start_url,
                self._requested_target_url or self._target.url,
            )

        return LinkingAnalyzeResponse(
            start_url=self._start_url,
            target_url=self._requested_target_url or self._target.url,
            fetch_summary=self._build_fetch_summary(
                html_fetch_mode=client.html_fetch_mode,
                sitemap_fetch_mode=client.sitemap_fetch_mode,
            ),
            fetch_stats=FetchStats(
                playwright_session_available=client.fetch_stats.playwright_session_available,
                html_playwright_attempts=client.fetch_stats.html_playwright_attempts,
                html_playwright_successes=client.fetch_stats.html_playwright_successes,
                html_playwright_failures=client.fetch_stats.html_playwright_failures,
                html_playwright_timeout_failures=client.fetch_stats.html_playwright_timeout_failures,
                html_playwright_http_status_failures=client.fetch_stats.html_playwright_http_status_failures,
                html_playwright_no_response_failures=client.fetch_stats.html_playwright_no_response_failures,
                html_playwright_other_failures=client.fetch_stats.html_playwright_other_failures,
                html_playwright_failure_status_codes=dict(client.fetch_stats.html_playwright_failure_status_codes),
                html_http_attempts=client.fetch_stats.html_http_attempts,
                html_http_successes=client.fetch_stats.html_http_successes,
                html_http_failures=client.fetch_stats.html_http_failures,
                html_http_fallback_successes=client.fetch_stats.html_http_fallback_successes,
                html_http_fallback_failures=client.fetch_stats.html_http_fallback_failures,
                sitemap_http_attempts=client.fetch_stats.sitemap_http_attempts,
                sitemap_http_successes=client.fetch_stats.sitemap_http_successes,
                sitemap_http_failures=client.fetch_stats.sitemap_http_failures,
            ),
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
            robots_checked=self._robots_snapshot.checked,
            robots_available=self._robots_snapshot.available,
            robots_obeyed=self._robots_snapshot.obeyed,
            robots_blocked_urls=len(self._robots_snapshot.blocked_urls),
            sitemap_checked=sitemap_checked,
            found_in_sitemap=found_in_sitemap,
            html_fetch_mode=client.html_fetch_mode,
            sitemap_fetch_mode=client.sitemap_fetch_mode,
            strategy=strategy,
            timings=timings,
            crawl_diagnostics=CrawlDiagnostics(
                crawl_max_depth=self._crawl_diagnostics.crawl_max_depth,
                budget_exhausted=self._crawl_diagnostics.budget_exhausted,
                depth_cutoff=self._crawl_diagnostics.depth_cutoff,
                level_truncated=self._crawl_diagnostics.level_truncated,
                truncated_levels=self._crawl_diagnostics.truncated_levels,
                truncated_nodes=self._crawl_diagnostics.truncated_nodes,
            ),
        )

    @staticmethod
    def _build_fetch_summary(*, html_fetch_mode: str, sitemap_fetch_mode: str) -> str:
        return f"{InternalLinkingResponseMixin._html_fetch_summary(html_fetch_mode)}; {InternalLinkingResponseMixin._sitemap_fetch_summary(sitemap_fetch_mode)}."

    @staticmethod
    def _html_fetch_summary(mode: str) -> str:
        if mode == "playwright":
            return "HTML: Playwright"
        if mode == "http-only":
            return "HTML: HTTP-only"
        if mode == "mixed":
            return "HTML: Playwright -> HTTP fallback"
        return "HTML: not requested"

    @staticmethod
    def _sitemap_fetch_summary(mode: str) -> str:
        if mode == "playwright":
            return "sitemap: Playwright"
        if mode == "http-only":
            return "sitemap: HTTP-only"
        if mode == "mixed":
            return "sitemap: Playwright -> HTTP fallback"
        return "sitemap: not requested"

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
