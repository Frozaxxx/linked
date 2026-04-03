from __future__ import annotations

from dataclasses import dataclass, field


LIVE_SITEMAP_STRATEGY = "bfs + live sitemap"
SITEMAP_RECOMMENDATION_FETCH_LIMIT = 8
SITEMAP_RECOMMENDATION_RANK_LIMIT = 64
SITEMAP_WAIT_TIMEOUT_SECONDS = 12.0
VERIFIED_PARENT_FETCH_LIMIT = 4
RECOMMENDATION_PHASE_RESERVE_RATIO = 0.25
RECOMMENDATION_PHASE_MAX_SECONDS = 8.0
MAX_RECOMMENDATION_SOURCE_DEPTH = 3


@dataclass(slots=True)
class SitemapSnapshot:
    checked: bool = False
    page_urls: set[str] = field(default_factory=set)
    found_target: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    completed: bool = False


@dataclass(slots=True)
class TargetVerificationResult:
    steps_to_target: int | None = None
    path: list[str] = field(default_factory=list)
    pages_fetched: int = 0
