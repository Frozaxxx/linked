from __future__ import annotations

import unittest

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


class InternalLinkingResponseFallbackTests(unittest.TestCase):
    def test_structural_recommendations_from_parent_branch_are_available_without_crawl(self) -> None:
        harness = ResponseFallbackHarness(
            "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri/about-glri/glri-focus-area-5-foundations/winter-observations-using-autonomous-mobile-platforms",
            "Winter observations using autonomous mobile platforms",
        )

        recommendations = harness._placement_recommender.build_structural_recommendations(
            sitemap_page_urls=set(harness._candidate_parent_urls()),
            excluded_urls=set(),
        )

        self.assertGreaterEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0].confidence, "fallback")
        self.assertEqual(
            recommendations[0].source_url,
            "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri",
        )

    def test_depth_based_strong_recommendations_use_discovered_urls(self) -> None:
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

        self.assertGreaterEqual(len(recommendations), 1)
        self.assertEqual(
            recommendations[0].source_url,
            "https://www.noaa.gov/regional-collaboration-network/regions-great-lakes/glri",
        )
        self.assertTrue(all(1 <= recommendation.source_depth <= 3 for recommendation in recommendations))

    def test_depth_based_soft_recommendations_return_fallback_candidates_from_discovered_urls(self) -> None:
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

        self.assertGreaterEqual(len(recommendations), 1)
        self.assertEqual(
            recommendations[0].source_url,
            "https://www.noaa.gov/regional-collaboration-network",
        )
        self.assertTrue(all(1 <= recommendation.source_depth <= 3 for recommendation in recommendations))
        self.assertNotIn("https://www.noaa.gov/education", {recommendation.source_url for recommendation in recommendations})


if __name__ == "__main__":
    unittest.main()
