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
    assert recommendations[0].confidence == "fallback"
    assert recommendations[0].source_url == "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri"


def test_depth_based_strong_recommendations_use_discovered_urls() -> None:
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
        soft=False,
    )

    assert len(recommendations) >= 1
    assert recommendations[0].source_url == "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri"
    assert all(1 <= recommendation.source_depth <= 3 for recommendation in recommendations)


def test_depth_based_soft_recommendations_return_fallback_candidates_from_discovered_urls() -> None:
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
        soft=True,
    )

    assert len(recommendations) >= 1
    assert recommendations[0].source_url == "https://www.noaa.gov/regional-collaboration-network"
    assert all(1 <= recommendation.source_depth <= 3 for recommendation in recommendations)
    assert "https://www.noaa.gov/education" not in {recommendation.source_url for recommendation in recommendations}
