from __future__ import annotations

from enum import Enum

from pydantic import AnyHttpUrl, BaseModel, Field

from app.settings import get_settings

try:
    from pydantic import ConfigDict

    PYDANTIC_V2 = True
except ImportError:  # pragma: no cover
    PYDANTIC_V2 = False


settings = get_settings()


class OptimizationStatus(str, Enum):
    GOOD = "good"
    BAD = "bad"
    NOT_FOUND = "not_found"


class LinkingAnalyzeRequest(BaseModel):
    target_url: AnyHttpUrl = Field(
        ...,
        description="URL целевой страницы. По нему анализатор находит главную страницу сайта и запускает обход.",
    )

    if PYDANTIC_V2:
        model_config = ConfigDict(
            extra="forbid",
            json_schema_extra={
                "example": {
                    "target_url": "https://example.com/catalog/target-page",
                }
            },
        )
    else:  # pragma: no cover

        class Config:
            extra = "forbid"
            schema_extra = {
                "example": {
                    "target_url": "https://example.com/catalog/target-page",
                }
            }


class AnalyzeTimings(BaseModel):
    total_ms: float
    match_ms: float | None
    sitemap_elapsed_ms: float | None
    sitemap_completed: bool


class CrawlDiagnostics(BaseModel):
    crawl_max_depth: int
    budget_exhausted: bool
    depth_cutoff: bool
    level_truncated: bool
    truncated_levels: int
    truncated_nodes: int


class FetchStats(BaseModel):
    playwright_session_available: bool
    html_playwright_attempts: int
    html_playwright_successes: int
    html_playwright_failures: int
    html_playwright_timeout_failures: int
    html_playwright_http_status_failures: int
    html_playwright_no_response_failures: int
    html_playwright_other_failures: int
    html_playwright_failure_status_codes: dict[str, int]
    html_http_attempts: int
    html_http_successes: int
    html_http_failures: int
    html_http_fallback_successes: int
    html_http_fallback_failures: int
    sitemap_http_attempts: int
    sitemap_http_successes: int
    sitemap_http_failures: int


class LinkingAnalyzeResponse(BaseModel):
    start_url: str
    target_url: str | None
    fetch_summary: str
    fetch_stats: FetchStats
    found: bool
    matched_by: list[str]
    steps_to_target: int | None
    path: list[str]
    optimization_status: OptimizationStatus
    message: str
    message_source: str | None = None
    message_error: str | None = None
    pages_fetched: int
    pages_discovered: int
    robots_checked: bool
    robots_available: bool
    robots_obeyed: bool
    robots_blocked_urls: int
    sitemap_checked: bool
    found_in_sitemap: bool
    html_fetch_mode: str
    sitemap_fetch_mode: str
    strategy: str
    timings: AnalyzeTimings
    crawl_diagnostics: CrawlDiagnostics

    if PYDANTIC_V2:
        model_config = {
            "json_schema_extra": {
                "example": {
                    "start_url": "https://example.com/",
                    "target_url": "https://example.com/catalog/target-page",
                    "fetch_summary": "HTML: Playwright; sitemap: HTTP-only.",
                    "fetch_stats": {
                        "playwright_session_available": True,
                        "html_playwright_attempts": 7,
                        "html_playwright_successes": 6,
                        "html_playwright_failures": 1,
                        "html_playwright_timeout_failures": 0,
                        "html_playwright_http_status_failures": 1,
                        "html_playwright_no_response_failures": 0,
                        "html_playwright_other_failures": 0,
                        "html_playwright_failure_status_codes": {"503": 1},
                        "html_http_attempts": 1,
                        "html_http_successes": 1,
                        "html_http_failures": 0,
                        "html_http_fallback_successes": 1,
                        "html_http_fallback_failures": 0,
                        "sitemap_http_attempts": 1,
                        "sitemap_http_successes": 1,
                        "sitemap_http_failures": 0,
                    },
                    "found": True,
                    "matched_by": ["url"],
                    "steps_to_target": 3,
                    "path": [
                        "https://example.com/",
                        "https://example.com/catalog",
                        "https://example.com/catalog/widgets",
                        "https://example.com/catalog/target-page",
                    ],
                    "optimization_status": "good",
                    "message": (
                        "Целевая страница находится в 3 шагах от стартовой при пороге 4, "
                        "поэтому перелинковка выглядит хорошей."
                    ),
                    "message_source": "llm",
                    "message_error": None,
                    "pages_fetched": 7,
                    "pages_discovered": 12,
                    "robots_checked": True,
                    "robots_available": True,
                    "robots_obeyed": True,
                    "robots_blocked_urls": 0,
                    "sitemap_checked": True,
                    "found_in_sitemap": True,
                    "html_fetch_mode": "playwright",
                    "sitemap_fetch_mode": "http-only",
                    "strategy": "bfs + live sitemap",
                    "timings": {
                        "total_ms": 184.231,
                        "match_ms": 184.231,
                        "sitemap_elapsed_ms": 61.418,
                        "sitemap_completed": False,
                    },
                    "crawl_diagnostics": {
                        "crawl_max_depth": settings.crawl_max_depth,
                        "budget_exhausted": False,
                        "depth_cutoff": False,
                        "level_truncated": False,
                        "truncated_levels": 0,
                        "truncated_nodes": 0,
                    },
                }
            }
        }
    else:  # pragma: no cover

        class Config:
            schema_extra = {
                "example": {
                    "start_url": "https://example.com/",
                    "target_url": "https://example.com/catalog/target-page",
                    "fetch_summary": "HTML: Playwright; sitemap: HTTP-only.",
                    "fetch_stats": {
                        "playwright_session_available": True,
                        "html_playwright_attempts": 7,
                        "html_playwright_successes": 6,
                        "html_playwright_failures": 1,
                        "html_playwright_timeout_failures": 0,
                        "html_playwright_http_status_failures": 1,
                        "html_playwright_no_response_failures": 0,
                        "html_playwright_other_failures": 0,
                        "html_playwright_failure_status_codes": {"503": 1},
                        "html_http_attempts": 1,
                        "html_http_successes": 1,
                        "html_http_failures": 0,
                        "html_http_fallback_successes": 1,
                        "html_http_fallback_failures": 0,
                        "sitemap_http_attempts": 1,
                        "sitemap_http_successes": 1,
                        "sitemap_http_failures": 0,
                    },
                    "found": True,
                    "matched_by": ["url"],
                    "steps_to_target": 3,
                    "path": [
                        "https://example.com/",
                        "https://example.com/catalog",
                        "https://example.com/catalog/widgets",
                        "https://example.com/catalog/target-page",
                    ],
                    "optimization_status": "good",
                    "message": (
                        "Целевая страница находится в 3 шагах от стартовой при пороге 4, "
                        "поэтому перелинковка выглядит хорошей."
                    ),
                    "message_source": "llm",
                    "message_error": None,
                    "pages_fetched": 7,
                    "pages_discovered": 12,
                    "robots_checked": True,
                    "robots_available": True,
                    "robots_obeyed": True,
                    "robots_blocked_urls": 0,
                    "sitemap_checked": True,
                    "found_in_sitemap": True,
                    "html_fetch_mode": "playwright",
                    "sitemap_fetch_mode": "http-only",
                    "strategy": "bfs + live sitemap",
                    "timings": {
                        "total_ms": 184.231,
                        "match_ms": 184.231,
                        "sitemap_elapsed_ms": 61.418,
                        "sitemap_completed": False,
                    },
                    "crawl_diagnostics": {
                        "crawl_max_depth": settings.crawl_max_depth,
                        "budget_exhausted": False,
                        "depth_cutoff": False,
                        "level_truncated": False,
                        "truncated_levels": 0,
                        "truncated_nodes": 0,
                    },
                }
            }
