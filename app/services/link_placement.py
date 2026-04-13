from __future__ import annotations

from app.services.link_placement_builders import LinkPlacementBuilderMixin
from app.services.link_placement_models import CrawledPageSnapshot, PlacementRecommendation
from app.services.link_placement_scoring import LinkPlacementScoringMixin
from app.services.link_placement_text import LinkPlacementTextMixin
from app.services.matcher import GENERIC_URL_TERMS, SearchTarget, normalize_text


class LinkPlacementRecommender(
    LinkPlacementBuilderMixin,
    LinkPlacementScoringMixin,
    LinkPlacementTextMixin,
):
    def __init__(self, *, target: SearchTarget, start_url: str, good_depth_threshold: int) -> None:
        self._target = target
        self._start_url = start_url
        self._good_depth_threshold = good_depth_threshold
        target_terms = set(target.thematic_terms or target.priority_terms)
        filtered_target_terms = {term for term in target_terms if term not in GENERIC_URL_TERMS}
        filtered_term_weights = {
            term: weight
            for term, weight in target.term_weights.items()
            if term not in GENERIC_URL_TERMS
        }
        self._target_terms = filtered_target_terms or target_terms
        self._target_term_weights = filtered_term_weights or target.term_weights
        self._target_signature_terms = set(target.signature_terms)
        self._target_branch_terms = set(target.branch_terms)
        self._target_core_branch_terms = set(target.core_branch_terms)
        self._normalized_target_title = normalize_text(target.title)
        self._normalized_target_text = normalize_text(target.text)


__all__ = [
    "CrawledPageSnapshot",
    "LinkPlacementRecommender",
    "PlacementRecommendation",
]
