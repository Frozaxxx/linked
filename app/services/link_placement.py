from __future__ import annotations

from app.services.link_placement_builders import LinkPlacementBuilderMixin
from app.services.link_placement_models import CrawledPageSnapshot, PlacementRecommendation
from app.services.link_placement_scoring import LinkPlacementScoringMixin
from app.services.link_placement_text import LinkPlacementTextMixin
from app.services.matcher import SearchTarget, normalize_text


class LinkPlacementRecommender(
    LinkPlacementBuilderMixin,
    LinkPlacementScoringMixin,
    LinkPlacementTextMixin,
):
    def __init__(self, *, target: SearchTarget, start_url: str, good_depth_threshold: int) -> None:
        self._target = target
        self._start_url = start_url
        self._good_depth_threshold = good_depth_threshold
        self._target_terms = set(target.thematic_terms or target.priority_terms)
        self._target_term_weights = target.term_weights
        self._target_total_weight = sum(self._target_term_weights.values()) or 1
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
