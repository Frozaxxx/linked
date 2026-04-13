from __future__ import annotations

from app.services.link_placement_models import (
    MAX_RECOMMENDATIONS,
    MAX_RECOMMENDATION_SOURCE_DEPTH,
    MIN_RECOMMENDATION_SOURCE_DEPTH,
    CrawledPageSnapshot,
    PlacementRecommendation,
    RankedRecommendation,
)


class LinkPlacementBuilderMixin:
    def build_soft_verified_recommendations(
        self,
        *,
        crawled_pages: dict[str, CrawledPageSnapshot],
        excluded_urls: set[str] | None = None,
    ) -> list[PlacementRecommendation]:
        ranked: dict[str, RankedRecommendation] = {}
        fallback_ranked: dict[str, RankedRecommendation] = {}
        excluded = excluded_urls or set()
        for snapshot in crawled_pages.values():
            if self._is_target_url(snapshot.url) or snapshot.url in excluded:
                continue
            if snapshot.depth is None or not self._is_allowed_source_depth(snapshot.depth):
                continue
            if not snapshot.is_indexable or snapshot.links_to_target:
                continue
            if self._is_technical_source(
                url=snapshot.url,
                normalized_title=snapshot.normalized_title,
                normalized_h1=snapshot.normalized_h1,
            ):
                continue
            score = self._soft_candidate_score(snapshot)
            if score <= 0:
                continue
            recommendation = PlacementRecommendation(
                source_url=snapshot.url,
                source_title=snapshot.title or None,
                source_depth=snapshot.depth,
                projected_steps_to_target=self._projected_steps(snapshot.depth),
                reason=self._build_soft_relevance_reason(snapshot),
                placement_hint=self._placement_hint(snapshot.depth),
                anchor_hint=self._anchor_hint(),
                confidence="soft",
            )
            self._remember_candidate(fallback_ranked, recommendation, self._fallback_candidate_score(snapshot) or score)
            self._remember_candidate(ranked, recommendation, score)

        return self._finalize_ranked_recommendations(ranked=ranked, fallback_ranked=fallback_ranked)

    def build_soft_url_only_recommendations(
        self,
        *,
        sitemap_page_urls: set[str],
        excluded_urls: set[str] | None = None,
        verified_depths: dict[str, int] | None = None,
    ) -> list[PlacementRecommendation]:
        if not verified_depths:
            return []

        ranked: list[tuple[int, PlacementRecommendation]] = []
        fallback_ranked: list[tuple[int, PlacementRecommendation]] = []
        excluded = excluded_urls or set()
        for url in sitemap_page_urls:
            if url in excluded or self._is_target_url(url) or self._is_technical_url(url):
                continue
            source_depth = verified_depths.get(url)
            if source_depth is None or not self._is_allowed_source_depth(source_depth):
                continue
            fallback_score = self.score_source_url_fallback(url)
            if fallback_score is None:
                continue
            score = self.score_source_url_soft(url)
            recommendation = PlacementRecommendation(
                source_url=url,
                source_title=None,
                source_depth=source_depth,
                projected_steps_to_target=self._projected_steps(source_depth),
                reason=self._build_soft_url_only_reason(url),
                placement_hint=self._placement_hint(source_depth),
                anchor_hint=self._anchor_hint(),
                confidence="soft",
            )
            fallback_ranked.append((fallback_score, recommendation))
            if score > 0:
                ranked.append((score, recommendation))

        return self._finalize_ranked_recommendations(ranked=ranked, fallback_ranked=fallback_ranked)

    def build_structural_recommendations(
        self,
        *,
        sitemap_page_urls: set[str],
        excluded_urls: set[str] | None = None,
    ) -> list[PlacementRecommendation]:
        ranked: list[tuple[int, PlacementRecommendation]] = []
        fallback_ranked: list[tuple[int, PlacementRecommendation]] = []
        excluded = excluded_urls or set()
        max_source_depth = min(self._good_depth_threshold - 1, MAX_RECOMMENDATION_SOURCE_DEPTH)
        if max_source_depth < 0:
            return []

        for url in sitemap_page_urls:
            if url in excluded or self._is_target_url(url) or self._is_technical_url(url):
                continue
            source_depth = self._estimated_structural_depth(url)
            if source_depth is None or source_depth > max_source_depth:
                continue
            if not self._is_allowed_source_depth(source_depth):
                continue
            projected_steps = self._projected_steps(source_depth)
            fallback_score = self.score_source_url_fallback(url)
            if fallback_score is None:
                continue
            soft_score = self.score_source_url_soft(url)
            recommendation = PlacementRecommendation(
                source_url=url,
                source_title=None,
                source_depth=source_depth,
                projected_steps_to_target=projected_steps,
                reason=self._build_soft_url_only_reason(url),
                placement_hint=self._placement_hint(source_depth),
                anchor_hint=self._anchor_hint(),
                confidence="soft",
            )
            fallback_ranked.append((fallback_score, recommendation))
            if soft_score > 0:
                ranked.append((soft_score, recommendation))

        return self._finalize_ranked_recommendations(ranked=ranked, fallback_ranked=fallback_ranked)

    @staticmethod
    def _is_allowed_source_depth(source_depth: int | None) -> bool:
        return (
            source_depth is not None
            and MIN_RECOMMENDATION_SOURCE_DEPTH <= source_depth <= MAX_RECOMMENDATION_SOURCE_DEPTH
        )

    @staticmethod
    def _finalize_ranked_recommendations(
        *,
        ranked: dict[str, RankedRecommendation] | list[tuple[int, PlacementRecommendation]],
        fallback_ranked: dict[str, RankedRecommendation] | list[tuple[int, PlacementRecommendation]],
    ) -> list[PlacementRecommendation]:
        selected: list[PlacementRecommendation] = []
        seen_urls: set[str] = set()
        for score, recommendation in LinkPlacementBuilderMixin._sorted_recommendation_items(ranked):
            if recommendation.source_url in seen_urls:
                continue
            seen_urls.add(recommendation.source_url)
            selected.append(recommendation)
            if len(selected) >= MAX_RECOMMENDATIONS:
                return selected
        for score, recommendation in LinkPlacementBuilderMixin._sorted_recommendation_items(fallback_ranked):
            if recommendation.source_url in seen_urls:
                continue
            seen_urls.add(recommendation.source_url)
            selected.append(recommendation)
            if len(selected) >= MAX_RECOMMENDATIONS:
                break
        return selected

    @staticmethod
    def _sorted_recommendation_items(
        items: dict[str, RankedRecommendation] | list[tuple[int, PlacementRecommendation]],
    ) -> list[tuple[int, PlacementRecommendation]]:
        if isinstance(items, dict):
            prepared = [(item.score, item.recommendation) for item in items.values()]
        else:
            prepared = list(items)
        return sorted(prepared, key=lambda item: (-item[0], item[1].source_url))
