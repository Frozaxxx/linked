from __future__ import annotations

import logging

from app.models import CrawledPageSnapshot
from app.schemas import CrawlDiagnostics, FetchStats, LinkingAnalyzeResponse, OptimizationStatus
from app.services.fetcher import FetchSession
from app.services.llm_summary import AnalysisMessageContext
from app.services.internal_linking.recommendations import InternalLinkingRecommendationMixin
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class InternalLinkingResponseMixin(InternalLinkingRecommendationMixin):
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
        can_use_url_only_recommendations = self._can_use_url_only_recommendations(
            pages_fetched=pages_fetched,
            pages_discovered=pages_discovered,
            sitemap_page_urls=sitemap_page_urls,
        )
        placement_recommendations = self._extend_placement_recommendations(
            [],
            self._placement_recommender.build_soft_verified_recommendations(
                crawled_pages=crawled_pages,
                excluded_urls=set(path),
            ),
        )

        if (
            self._needs_more_placement_recommendations(placement_recommendations)
            and discovered_depths
            and can_use_url_only_recommendations
        ):
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

        if (
            self._needs_more_placement_recommendations(placement_recommendations)
            and self._target.url
            and can_use_url_only_recommendations
        ):
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

        if self._needs_more_placement_recommendations(placement_recommendations) and can_use_url_only_recommendations:
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
        if status == OptimizationStatus.GOOD:
            placement_recommendations = []

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
            "Fetch stats: start_url=%s playwright_available=%s html_playwright_attempts=%s html_playwright_successes=%s html_playwright_failures=%s html_playwright_timeout_failures=%s html_playwright_http_status_failures=%s html_playwright_no_response_failures=%s html_playwright_other_failures=%s html_playwright_failure_status_codes=%s html_playwright_partial_successes=%s html_http_attempts=%s html_http_successes=%s html_http_failures=%s html_http_timeout_failures=%s html_http_status_failures=%s html_http_request_failures=%s html_http_failure_status_codes=%s html_http_partial_successes=%s html_http_range_attempts=%s html_http_range_successes=%s html_http_range_failures=%s html_http_fallback_successes=%s html_http_fallback_failures=%s sitemap_http_attempts=%s sitemap_http_successes=%s sitemap_http_failures=%s sitemap_http_timeout_failures=%s sitemap_http_status_failures=%s sitemap_http_request_failures=%s sitemap_http_failure_status_codes=%s",
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
            client.fetch_stats.html_playwright_partial_successes,
            client.fetch_stats.html_http_attempts,
            client.fetch_stats.html_http_successes,
            client.fetch_stats.html_http_failures,
            client.fetch_stats.html_http_timeout_failures,
            client.fetch_stats.html_http_status_failures,
            client.fetch_stats.html_http_request_failures,
            client.fetch_stats.html_http_failure_status_codes,
            client.fetch_stats.html_http_partial_successes,
            client.fetch_stats.html_http_range_attempts,
            client.fetch_stats.html_http_range_successes,
            client.fetch_stats.html_http_range_failures,
            client.fetch_stats.html_http_fallback_successes,
            client.fetch_stats.html_http_fallback_failures,
            client.fetch_stats.sitemap_http_attempts,
            client.fetch_stats.sitemap_http_successes,
            client.fetch_stats.sitemap_http_failures,
            client.fetch_stats.sitemap_http_timeout_failures,
            client.fetch_stats.sitemap_http_status_failures,
            client.fetch_stats.sitemap_http_request_failures,
            client.fetch_stats.sitemap_http_failure_status_codes,
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
                html_playwright_partial_successes=client.fetch_stats.html_playwright_partial_successes,
                html_http_attempts=client.fetch_stats.html_http_attempts,
                html_http_successes=client.fetch_stats.html_http_successes,
                html_http_failures=client.fetch_stats.html_http_failures,
                html_http_timeout_failures=client.fetch_stats.html_http_timeout_failures,
                html_http_status_failures=client.fetch_stats.html_http_status_failures,
                html_http_request_failures=client.fetch_stats.html_http_request_failures,
                html_http_failure_status_codes=dict(client.fetch_stats.html_http_failure_status_codes),
                html_http_partial_successes=client.fetch_stats.html_http_partial_successes,
                html_http_range_attempts=client.fetch_stats.html_http_range_attempts,
                html_http_range_successes=client.fetch_stats.html_http_range_successes,
                html_http_range_failures=client.fetch_stats.html_http_range_failures,
                html_http_fallback_successes=client.fetch_stats.html_http_fallback_successes,
                html_http_fallback_failures=client.fetch_stats.html_http_fallback_failures,
                sitemap_http_attempts=client.fetch_stats.sitemap_http_attempts,
                sitemap_http_successes=client.fetch_stats.sitemap_http_successes,
                sitemap_http_failures=client.fetch_stats.sitemap_http_failures,
                sitemap_http_timeout_failures=client.fetch_stats.sitemap_http_timeout_failures,
                sitemap_http_status_failures=client.fetch_stats.sitemap_http_status_failures,
                sitemap_http_request_failures=client.fetch_stats.sitemap_http_request_failures,
                sitemap_http_failure_status_codes=dict(client.fetch_stats.sitemap_http_failure_status_codes),
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
    def _can_use_url_only_recommendations(
        *,
        pages_fetched: int,
        pages_discovered: int,
        sitemap_page_urls: set[str],
    ) -> bool:
        return pages_fetched > 0 or pages_discovered > 1 or bool(sitemap_page_urls)

    @staticmethod
    def _build_fetch_summary(*, html_fetch_mode: str, sitemap_fetch_mode: str) -> str:
        return f"{InternalLinkingResponseMixin._html_fetch_summary(html_fetch_mode)}; {InternalLinkingResponseMixin._sitemap_fetch_summary(sitemap_fetch_mode)}."

    @staticmethod
    def _html_fetch_summary(mode: str) -> str:
        if mode == "playwright":
            return "HTML: Playwright"
        if mode == "http-only":
            return "HTML: HTTP-only"
        if mode == "http-to-playwright":
            return "HTML: HTTP -> Playwright fallback"
        if mode == "playwright-to-http":
            return "HTML: Playwright -> HTTP fallback"
        if mode == "mixed":
            return "HTML: mixed transports"
        return "HTML: not requested"

    @staticmethod
    def _sitemap_fetch_summary(mode: str) -> str:
        if mode == "playwright":
            return "sitemap: Playwright"
        if mode == "http-only":
            return "sitemap: HTTP-only"
        if mode == "http-to-playwright":
            return "sitemap: HTTP -> Playwright fallback"
        if mode == "playwright-to-http":
            return "sitemap: Playwright -> HTTP fallback"
        if mode == "mixed":
            return "sitemap: mixed transports"
        return "sitemap: not requested"
