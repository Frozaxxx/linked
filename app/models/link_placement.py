from __future__ import annotations

from app.models.base import SeoLinkedModel


class PlacementRecommendation(SeoLinkedModel):
    source_url: str
    source_title: str | None
    source_depth: int | None
    projected_steps_to_target: int | None
    reason: str
    placement_hint: str
    anchor_hint: str | None
    confidence: str = "soft"


class CrawledPageSnapshot(SeoLinkedModel):
    url: str
    title: str
    depth: int | None
    normalized_title: str
    normalized_h1: str
    normalized_text: str
    url_terms: frozenset[str]
    title_terms: frozenset[str]
    h1_terms: frozenset[str]
    body_terms: frozenset[str]
    is_indexable: bool
    links_to_target: bool


class RankedRecommendation(SeoLinkedModel):
    recommendation: PlacementRecommendation
    score: int
