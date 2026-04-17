from __future__ import annotations

from types import SimpleNamespace

from app.services.internal_linking.response import InternalLinkingResponseMixin
from app.services.internal_linking.discovery import InternalLinkingDiscoveryMixin
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

    def _remaining_fetch_budget_seconds(self) -> float | None:
        return 120.0

    def _is_allowed_by_robots(self, url: str) -> bool:
        return True


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
    assert recommendations[0].source_url == "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri"


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
