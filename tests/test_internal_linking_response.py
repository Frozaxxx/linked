from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.models import CrawlDiagnosticsSnapshot
from app.services.fetcher import FetchSession, FetchedDocument
from app.services.internal_linking.recommendations import InternalLinkingRecommendationMixin
from app.services.internal_linking.response import InternalLinkingResponseMixin
from app.services.internal_linking.discovery import InternalLinkingDiscoveryMixin
from app.services.internal_linking.runtime import InternalLinkingRuntimeMixin
from app.services.internal_linking.verification import InternalLinkingVerificationMixin
from app.services.link_placement import LinkPlacementRecommender
from app.services.matcher import SearchTarget


class ResponseFallbackHarness(InternalLinkingResponseMixin):
    def __init__(self, target_url: str, target_title: str | None = None) -> None:
        self._target = SearchTarget(url=target_url, title=target_title, text=None)
        self._placement_recommender = LinkPlacementRecommender(
            target=self._target,
            start_url="https://www.noaa.gov/",
            good_depth_threshold=4,
        )


class TargetMetadataHarness(InternalLinkingDiscoveryMixin):
    def __init__(self) -> None:
        self._requested_target_url = "https://example.com/target"
        self._allowed_host = "example.com"
        self._fetcher = SimpleNamespace(fetch=None)
        self.replacements: list[dict] = []

    def _remaining_fetch_budget_seconds(self) -> float | None:
        return 120.0

    def _is_allowed_by_robots(self, url: str) -> bool:
        return True

    def _replace_target(self, **kwargs) -> None:
        self.replacements.append(kwargs)


class DirectParentBridgeHarness(
    InternalLinkingVerificationMixin,
    InternalLinkingRecommendationMixin,
    InternalLinkingRuntimeMixin,
):
    def __init__(self, *, parent_body: str) -> None:
        self._start_url = "https://example.com/"
        self._allowed_host = "example.com"
        self._target = SearchTarget(url="https://example.com/section/topic/target", title=None, text=None)
        self._placement_recommender = LinkPlacementRecommender(
            target=self._target,
            start_url=self._start_url,
            good_depth_threshold=4,
        )
        self._deadline_started_at = 0.0
        self._crawl_diagnostics = CrawlDiagnosticsSnapshot(crawl_max_depth=4)
        self._parent_body = parent_body
        self.fetch_calls: list[dict] = []
        self._fetcher = SimpleNamespace(fetch=self._fake_fetch)

    async def _fake_fetch(self, client: FetchSession, url: str, **kwargs):
        self.fetch_calls.append({"url": url, **kwargs})
        if url != "https://example.com/section/topic":
            return None
        return FetchedDocument(
            requested_url=url,
            final_url=url,
            body=self._parent_body,
            content_type="text/html",
            body_bytes=self._parent_body.encode("utf-8"),
        )

    def _remaining_budget_seconds(self) -> float | None:
        return 120.0

    def _remaining_fetch_budget_seconds(self) -> float | None:
        return 120.0

    def _is_allowed_by_robots(self, url: str) -> bool:
        return True

    def _record_html_fetch_failure_status(self, status_code: int, url: str) -> None:
        return None


def test_fetch_summary_reports_transport_at_top_level() -> None:
    summary = ResponseFallbackHarness._build_fetch_summary(
        html_fetch_mode="http-to-playwright",
        sitemap_fetch_mode="http-only",
    )

    assert summary == "HTML: HTTP -> Playwright fallback; sitemap: HTTP-only."


def test_target_metadata_timeout_is_capped_by_single_request_timeout(monkeypatch) -> None:
    from app.settings import get_settings

    monkeypatch.setattr(get_settings(), "request_timeout_seconds", 20.0)
    harness = TargetMetadataHarness()

    assert harness._target_metadata_timeout_seconds() == 20.0


@pytest.mark.asyncio
async def test_target_metadata_redirect_keeps_requested_url_as_match_target() -> None:
    harness = TargetMetadataHarness()
    harness._requested_target_url = "https://example.com/section/topic/target"
    harness._fetcher = SimpleNamespace(fetch=pytest.fail)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        if str(request.url) == "https://example.com/short-target":
            return httpx.Response(200, request=request)
        return httpx.Response(
            301,
            headers={"location": "https://example.com/short-target"},
            request=request,
        )

    session = FetchSession(
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://example.com",
        )
    )
    try:
        resolved = await harness._resolve_target_metadata(session)
    finally:
        await session.http_client.aclose()

    assert resolved == 0
    assert harness.replacements == []


@pytest.mark.asyncio
async def test_direct_parent_bridge_confirms_target_link_without_broad_crawl() -> None:
    harness = DirectParentBridgeHarness(
        parent_body="<html><body><a href='/section/topic/target'>Target</a></body></html>",
    )
    session = FetchSession(http_client=httpx.AsyncClient())
    try:
        result = await harness._verify_direct_target_parent_bridge(
            client=session,
            crawled_pages={},
            max_depth=4,
        )
    finally:
        await session.http_client.aclose()

    assert result.steps_to_target == 2
    assert result.path == [
        "https://example.com/",
        "https://example.com/section/topic",
        "https://example.com/section/topic/target",
    ]
    assert harness.fetch_calls == [
        {
            "url": "https://example.com/section/topic",
            "total_timeout_seconds": 120.0,
            "failure_status_callback": harness._record_html_fetch_failure_status,
            "prefer_browser": True,
        }
    ]


@pytest.mark.asyncio
async def test_direct_parent_bridge_ignores_parent_without_target_link() -> None:
    harness = DirectParentBridgeHarness(
        parent_body="<html><body><a href='/section/topic/other'>Other</a></body></html>",
    )
    session = FetchSession(http_client=httpx.AsyncClient())
    try:
        result = await harness._verify_direct_target_parent_bridge(
            client=session,
            crawled_pages={},
            max_depth=4,
        )
    finally:
        await session.http_client.aclose()

    assert result.steps_to_target is None
    assert result.path == []


@pytest.mark.asyncio
async def test_direct_parent_bridge_does_not_match_redirect_canonical_short_url() -> None:
    harness = DirectParentBridgeHarness(
        parent_body="<html><body><a href='/short-target'>Canonical target</a></body></html>",
    )
    harness._target = SearchTarget(
        url="https://example.com/section/topic/target",
        title=None,
        text=None,
        canonical_url="https://example.com/short-target",
        equivalent_urls=("https://example.com/short-target",),
    )
    harness._placement_recommender = LinkPlacementRecommender(
        target=harness._target,
        start_url=harness._start_url,
        good_depth_threshold=4,
    )
    session = FetchSession(http_client=httpx.AsyncClient())
    try:
        result = await harness._verify_direct_target_parent_bridge(
            client=session,
            crawled_pages={},
            max_depth=4,
        )
    finally:
        await session.http_client.aclose()

    assert result.steps_to_target is None
    assert result.path == []


def test_url_only_recommendations_require_some_crawl_or_sitemap_evidence() -> None:
    assert not ResponseFallbackHarness._can_use_url_only_recommendations(
        pages_fetched=0,
        pages_discovered=1,
        sitemap_page_urls=set(),
    )
    assert ResponseFallbackHarness._can_use_url_only_recommendations(
        pages_fetched=0,
        pages_discovered=1,
        sitemap_page_urls={"https://www.noaa.gov/sitemap-page"},
    )


def test_structural_recommendations_from_parent_branch_are_available_without_crawl() -> None:
    harness = ResponseFallbackHarness(
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri/about-glri/glri-focus-area-5-foundations/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )

    recommendations = harness._placement_recommender.build_structural_recommendations(
        sitemap_page_urls=set(harness._candidate_parent_urls()),
        excluded_urls=set(),
    )

    assert len(recommendations) >= 1
    assert recommendations[0].confidence == "soft"
    assert [recommendation.source_url for recommendation in recommendations] == [
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri",
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes",
        "https://www.noaa.gov/regional-collaboration-network",
    ]


def test_depth_based_soft_recommendations_use_discovered_urls() -> None:
    harness = ResponseFallbackHarness(
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri/about-glri/glri-focus-area-5-foundations/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://www.noaa.gov/": 0,
            "https://www.noaa.gov/regional-collaboration-network": 1,
            "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes": 2,
            "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri": 3,
            "https://www.noaa.gov/news-release": 1,
        },
        path=[],
    )

    assert len(recommendations) >= 1
    assert recommendations[0].source_url == "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri"
    assert all(1 <= recommendation.source_depth <= 3 for recommendation in recommendations)


def test_depth_based_soft_recommendations_skip_weak_generic_branch_urls() -> None:
    harness = ResponseFallbackHarness(
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri/about-glri/glri-focus-area-5-foundations/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://www.noaa.gov/": 0,
            "https://www.noaa.gov/regional-collaboration-network": 1,
            "https://www.noaa.gov/education": 1,
            "https://www.noaa.gov/news-release": 1,
        },
        path=[],
    )

    assert recommendations == []


def test_depth_based_soft_recommendations_skip_news_release_branches() -> None:
    harness = ResponseFallbackHarness(
        "https://example.com/platform/autonomous-mobile-observation",
        "Autonomous mobile observation",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://example.com/news-release/autonomous-update": 1,
            "https://example.com/new-release/mobile-observation": 1,
            "https://example.com/platform/autonomous-mobile-overview": 2,
        },
        path=[],
    )

    assert [recommendation.source_url for recommendation in recommendations] == [
        "https://example.com/platform/autonomous-mobile-overview",
    ]


def test_soft_recommendations_do_not_prioritize_shallow_url_over_better_match() -> None:
    harness = ResponseFallbackHarness(
        "https://example.com/research/autonomous-mobile-platforms",
        "Autonomous mobile platforms",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://example.com/mobile": 1,
            "https://example.com/research/autonomous-mobile-platforms-overview": 3,
        },
        path=[],
    )

    assert len(recommendations) == 1
    assert recommendations[0].source_url == "https://example.com/research/autonomous-mobile-platforms-overview"


def test_soft_verified_recommendations_ignore_generic_weak_term_matches() -> None:
    harness = ResponseFallbackHarness(
        "https://example.com/research/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )
    snapshot = harness._placement_recommender.build_snapshot(
        url="https://example.com/help/how-to-use-tools",
        title="How to use tools",
        h1="How to use tools",
        depth=1,
        text="Use these tools to find information.",
    )

    recommendations = harness._placement_recommender.build_soft_verified_recommendations(
        crawled_pages={snapshot.url: snapshot},
        excluded_urls=set(),
    )

    assert recommendations == []


def test_depth_based_soft_recommendations_skip_single_weak_term_fallbacks() -> None:
    harness = ResponseFallbackHarness(
        "https://example.com/platform/autonomous-mobile-observation",
        "Autonomous mobile observation",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://example.com/platform/autonomous-mobile-overview": 2,
            "https://example.com/sensors/mobile-sensors": 2,
            "https://example.com/weather/observation-systems": 3,
            "https://example.com/docs/archive": 1,
        },
        path=[],
    )

    assert len(recommendations) == 1
    assert recommendations[0].source_url == "https://example.com/platform/autonomous-mobile-overview"


def test_soft_verified_recommendations_use_title_and_h1_semantics() -> None:
    harness = ResponseFallbackHarness(
        "https://example.com/research/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )
    snapshot = harness._placement_recommender.build_snapshot(
        url="https://example.com/research/related-project",
        title="Autonomous mobile platform field work",
        h1="Winter observation systems",
        depth=2,
        text="Short overview page.",
    )

    recommendations = harness._placement_recommender.build_soft_verified_recommendations(
        crawled_pages={snapshot.url: snapshot},
        excluded_urls=set(),
    )

    assert len(recommendations) == 1
    assert recommendations[0].source_url == snapshot.url
    assert "title/H1" in recommendations[0].reason


def test_soft_verified_recommendations_skip_single_title_h1_term_matches() -> None:
    harness = ResponseFallbackHarness(
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri/about-glri/glri-focus-area-5-foundations/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )
    mobile_snapshot = harness._placement_recommender.build_snapshot(
        url="https://www.noaa.gov/media-advisory/noaa-to-hold-ribbon-cutting-for-new-mobile-radars-to-track-tornadoes-advance-severe-weather",
        title="NOAA to hold ribbon cutting for new mobile radars",
        h1="NOAA to hold ribbon cutting for new mobile radars",
        depth=2,
        text="Short media advisory.",
    )
    observation_snapshot = harness._placement_recommender.build_snapshot(
        url="https://www.noaa.gov/education/resource-collections/weather-atmosphere/weather-observations",
        title="Weather observations",
        h1="Weather observations",
        depth=2,
        text="Short resource page.",
    )
    strong_snapshot = harness._placement_recommender.build_snapshot(
        url="https://www.noaa.gov/late-fall-winter-and-under-ice-observations-on-mobile-platforms",
        title="Late fall, winter and under-ice observations on mobile platforms",
        h1="Late fall, winter and under-ice observations on mobile platforms",
        depth=1,
        text="Autonomous platforms collect winter observations.",
    )

    recommendations = harness._placement_recommender.build_soft_verified_recommendations(
        crawled_pages={
            mobile_snapshot.url: mobile_snapshot,
            observation_snapshot.url: observation_snapshot,
            strong_snapshot.url: strong_snapshot,
        },
        excluded_urls=set(),
    )

    assert [recommendation.source_url for recommendation in recommendations] == [strong_snapshot.url]


def test_soft_verified_recommendations_skip_broad_branch_only_title_h1_matches() -> None:
    harness = ResponseFallbackHarness(
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri/about-glri/glri-focus-area-5-foundations/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )
    snapshot = harness._placement_recommender.build_snapshot(
        url="https://www.noaa.gov/education/resource-collections/freshwater/great-lakes-ecoregion",
        title="Great Lakes ecoregion",
        h1="Great Lakes ecoregion",
        depth=2,
        text="Freshwater education resource.",
    )

    recommendations = harness._placement_recommender.build_soft_verified_recommendations(
        crawled_pages={snapshot.url: snapshot},
        excluded_urls=set(),
    )

    assert recommendations == []


def test_soft_verified_recommendations_prioritize_thematic_pages_over_structural_parents() -> None:
    harness = ResponseFallbackHarness(
        "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri/about-glri/glri-focus-area-5-foundations/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )
    parent_snapshot = harness._placement_recommender.build_snapshot(
        url="https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri",
        title="Great Lakes Restoration Initiative",
        h1="Great Lakes Restoration Initiative",
        depth=3,
        text="Regional GLRI coordination.",
    )
    thematic_snapshot = harness._placement_recommender.build_snapshot(
        url="https://www.noaa.gov/late-fall-winter-and-under-ice-observations-on-mobile-platforms",
        title="Late fall, winter and under-ice observations on mobile platforms",
        h1="Late fall, winter and under-ice observations on mobile platforms",
        depth=1,
        text="Autonomous platforms collect winter observations.",
    )

    recommendations = harness._placement_recommender.build_soft_verified_recommendations(
        crawled_pages={
            parent_snapshot.url: parent_snapshot,
            thematic_snapshot.url: thematic_snapshot,
        },
        excluded_urls=set(),
    )

    assert [recommendation.source_url for recommendation in recommendations] == [
        thematic_snapshot.url,
        parent_snapshot.url,
    ]
    assert recommendations[1].reason == (
        "Проверенная страница из соседнего раздела сайта, которую можно использовать как рабочего донора."
    )


def test_recommendations_skip_comment_modal_urls() -> None:
    harness = ResponseFallbackHarness(
        "https://www.noaa.gov/winter-observations-using-autonomous-mobile-platforms",
        "Winter observations using autonomous mobile platforms",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://www.noaa.gov/noaa_landing_page/comment_modal?email=webmaster%40noaa.gov&url=https%3A%2F%2Fwww.noaa.gov%2Fwinter-observations-using-autonomous-mobile-platforms": 2,
            "https://www.noaa.gov/research/autonomous-mobile-platforms": 2,
        },
        path=[],
    )

    assert all("comment_modal" not in recommendation.source_url for recommendation in recommendations)


def test_news_article_recommendations_skip_different_dated_articles_when_only_section_matches() -> None:
    harness = ResponseFallbackHarness(
        "https://www.rbc.ru/economics/2019/12/20/5dfc5a679a7947d1b5b3e8a9",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://www.rbc.ru/economics/01/03/2017/58b678bb9a7947cd9d432bff": 1,
            "https://www.rbc.ru/economics/01/05/2016/5725bbd99a7947f97e8a3e18": 1,
            "https://www.rbc.ru/economics": 1,
            "https://www.rbc.ru/economics/2019": 2,
            "https://www.rbc.ru/economics/2019/12": 3,
        },
        path=[],
    )

    assert [recommendation.source_url for recommendation in recommendations] == [
        "https://www.rbc.ru/economics/2019/12",
        "https://www.rbc.ru/economics/2019",
        "https://www.rbc.ru/economics",
    ]


def test_structural_recommendations_do_not_count_rubrics_prefix_as_depth() -> None:
    harness = ResponseFallbackHarness(
        "https://lenta.ru/rubrics/society/2020/03/15/coronavirus",
    )

    recommendations = harness._placement_recommender.build_structural_recommendations(
        sitemap_page_urls=set(harness._candidate_parent_urls()),
        excluded_urls=set(),
    )

    assert [recommendation.source_url for recommendation in recommendations] == [
        "https://lenta.ru/rubrics/society/2020/03",
        "https://lenta.ru/rubrics/society/2020",
        "https://lenta.ru/rubrics/society",
    ]
    assert [recommendation.source_depth for recommendation in recommendations] == [3, 2, 1]
