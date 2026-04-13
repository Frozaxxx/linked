from __future__ import annotations

from app.services.internal_linking_response import InternalLinkingResponseMixin
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


def test_fetch_summary_reports_transport_at_top_level() -> None:
    summary = ResponseFallbackHarness._build_fetch_summary(
        html_fetch_mode="mixed",
        sitemap_fetch_mode="http-only",
    )

    assert summary == "HTML: Playwright -> HTTP fallback; sitemap: HTTP-only."


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

    assert len(recommendations) >= 2
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


def test_depth_based_soft_recommendations_fill_to_three_with_best_scored_fallbacks() -> None:
    harness = ResponseFallbackHarness(
        "https://example.com/platform/autonomous-mobile-observation",
        "Autonomous mobile observation",
    )

    recommendations = harness._build_depth_based_recommendations(
        candidate_depths={
            "https://example.com/platform/autonomous-mobile-overview": 2,
            "https://example.com/platform/mobile-sensors": 2,
            "https://example.com/platform/observation-systems": 3,
            "https://example.com/docs/archive": 1,
        },
        path=[],
    )

    assert len(recommendations) == 3
    assert recommendations[0].source_url == "https://example.com/platform/autonomous-mobile-overview"
    assert {recommendation.source_url for recommendation in recommendations} == {
        "https://example.com/platform/autonomous-mobile-overview",
        "https://example.com/platform/mobile-sensors",
        "https://example.com/platform/observation-systems",
    }
