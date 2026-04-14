from __future__ import annotations

from pydantic import Field

from app.models.base import SeoLinkedModel
from app.models.link_placement import PlacementRecommendation


class AnalysisMessageContext(SeoLinkedModel):
    start_url: str
    target_url: str | None
    target_title: str | None
    found: bool
    optimization_status: str
    steps_to_target: int | None
    good_depth_threshold: int
    search_depth_limit: int
    matched_by: list[str]
    pages_fetched: int
    pages_discovered: int
    sitemap_checked: bool
    found_in_sitemap: bool
    html_fetch_mode: str
    sitemap_fetch_mode: str
    crawl_max_depth: int
    budget_exhausted: bool
    depth_cutoff: bool
    level_truncated: bool
    truncated_levels: int
    truncated_nodes: int
    path: list[str]
    placement_recommendations: list[PlacementRecommendation] = Field(default_factory=list)


class GeneratedAnalysisMessage(SeoLinkedModel):
    text: str
    source: str
    error: str | None = None
