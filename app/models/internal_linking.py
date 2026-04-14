from __future__ import annotations

from pydantic import Field

from app.models.base import SeoLinkedModel


class SitemapSnapshot(SeoLinkedModel):
    checked: bool = False
    page_urls: set[str] = Field(default_factory=set)
    found_target: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    completed: bool = False


class RobotsSnapshot(SeoLinkedModel):
    checked: bool = False
    available: bool = False
    obeyed: bool = False
    sitemap_urls: set[str] = Field(default_factory=set)
    blocked_urls: set[str] = Field(default_factory=set)


class CrawlDiagnosticsSnapshot(SeoLinkedModel):
    crawl_max_depth: int
    budget_exhausted: bool = False
    depth_cutoff: bool = False
    level_truncated: bool = False
    truncated_levels: int = 0
    truncated_nodes: int = 0


class TargetVerificationResult(SeoLinkedModel):
    steps_to_target: int | None = None
    path: list[str] = Field(default_factory=list)
    pages_fetched: int = 0
