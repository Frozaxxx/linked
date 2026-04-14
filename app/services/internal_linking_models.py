from __future__ import annotations

from app.models import (
    CrawlDiagnosticsSnapshot,
    RobotsSnapshot,
    SitemapSnapshot,
    TargetVerificationResult,
)
from app.services.internal_linking.constants import (
    LIVE_SITEMAP_STRATEGY,
    MAX_RECOMMENDATION_SOURCE_DEPTH,
    RECOMMENDATION_PHASE_MAX_SECONDS,
    RECOMMENDATION_PHASE_RESERVE_RATIO,
    SITEMAP_RECOMMENDATION_FETCH_LIMIT,
    SITEMAP_RECOMMENDATION_RANK_LIMIT,
    SITEMAP_WAIT_TIMEOUT_SECONDS,
    VERIFIED_PARENT_FETCH_LIMIT,
)
