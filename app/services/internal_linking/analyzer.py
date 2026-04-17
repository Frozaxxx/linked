from __future__ import annotations

import asyncio
import logging

import httpx

from app.models import CrawlDiagnosticsSnapshot, RobotsSnapshot, SearchTarget
from app.schemas import LinkingAnalyzeRequest
from app.services.fetcher import AsyncFetcher
from app.services.frontier import score_link
from app.services.internal_linking.discovery import InternalLinkingDiscoveryMixin
from app.services.internal_linking.response import InternalLinkingResponseMixin
from app.services.internal_linking.runtime import InternalLinkingRuntimeMixin
from app.services.internal_linking.verification import InternalLinkingVerificationMixin
from app.services.internal_linking.workflow import InternalLinkingWorkflowMixin
from app.services.link_placement import LinkPlacementRecommender
from app.services.llm_reranker import PlacementRecommendationReranker
from app.services.llm_summary import LinkingAnalysisMessageGenerator
from app.services.parser import canonical_host, get_site_root, normalize_url
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class InternalLinkingAnalyzer(
    InternalLinkingWorkflowMixin,
    InternalLinkingDiscoveryMixin,
    InternalLinkingResponseMixin,
    InternalLinkingVerificationMixin,
    InternalLinkingRuntimeMixin,
):
    def __init__(
        self,
        request: LinkingAnalyzeRequest,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._requested_target_url = normalize_url(str(request.target_url))
        self._start_url = self._resolve_start_url(target_url=self._requested_target_url)
        self._allowed_host = canonical_host(httpx.URL(self._start_url).host if self._start_url else None)
        self._requested_target_title = None
        self._requested_target_text = None
        self._target = SearchTarget(
            url=self._requested_target_url,
            title=self._requested_target_title,
            text=self._requested_target_text,
        )
        self._fetcher = AsyncFetcher(
            timeout_seconds=settings.request_timeout_seconds,
            retry_count=settings.request_retry_count,
            transport=transport,
        )
        self._placement_recommender = self._build_placement_recommender()
        self._placement_reranker = PlacementRecommendationReranker()
        self._message_generator = LinkingAnalysisMessageGenerator()
        self._semaphore = asyncio.Semaphore(settings.crawl_concurrency)
        self._deadline_started_at: float | None = None
        self._crawl_diagnostics = CrawlDiagnosticsSnapshot(crawl_max_depth=settings.crawl_max_depth)
        self._robots_snapshot = RobotsSnapshot(obeyed=settings.obey_robots_txt)
        self._robots_policy = None
        self._html_403_branch_counts: dict[str, int] = {}
        self._html_403_blocked_branches: set[str] = set()

    @staticmethod
    def _resolve_start_url(*, target_url: str | None) -> str | None:
        if not target_url:
            return None
        return normalize_url(get_site_root(target_url))

    def _build_placement_recommender(self) -> LinkPlacementRecommender:
        return LinkPlacementRecommender(
            target=self._target,
            start_url=self._start_url or "",
            good_depth_threshold=settings.good_depth_threshold,
        )

    def _score_discovered_link(self, url: str, anchor_text: str) -> int:
        score = score_link(url, anchor_text, self._target.priority_terms)
        if self._target.url:
            score += self._candidate_branch_bonus(url, {self._target.url})
        return score

    def _replace_target(
        self,
        *,
        primary_url: str | None = None,
        title: str | None = None,
        equivalent_urls: set[str] | None = None,
        canonical_url: str | None = None,
    ) -> None:
        resolved_target_url = primary_url or self._requested_target_url
        normalized_equivalents = tuple(
            sorted({url for url in (equivalent_urls or set()) if url and url != resolved_target_url})
        )
        self._target = SearchTarget(
            url=resolved_target_url,
            title=title if title is not None else self._requested_target_title,
            text=self._requested_target_text,
            canonical_url=canonical_url if canonical_url != resolved_target_url else None,
            equivalent_urls=normalized_equivalents,
        )
        self._placement_recommender = self._build_placement_recommender()

    def _target_url_match_reason(self, url: str) -> str:
        return self._target.url_match_reason(url) or "url"

    def _is_allowed_by_robots(self, url: str) -> bool:
        if not settings.obey_robots_txt or self._robots_policy is None:
            return True
        allowed = self._robots_policy.is_allowed(url)
        if not allowed:
            self._robots_snapshot.blocked_urls.add(url)
        return allowed
