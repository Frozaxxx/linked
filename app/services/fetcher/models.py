from __future__ import annotations

from pydantic import Field

from app.models import SeoLinkedModel


class FetchedDocument(SeoLinkedModel):
    requested_url: str
    final_url: str
    body: str
    content_type: str
    body_bytes: bytes | None = None


class FetchTransportStats(SeoLinkedModel):
    playwright_session_available: bool = False
    html_playwright_attempts: int = 0
    html_playwright_successes: int = 0
    html_playwright_failures: int = 0
    html_playwright_timeout_failures: int = 0
    html_playwright_http_status_failures: int = 0
    html_playwright_no_response_failures: int = 0
    html_playwright_other_failures: int = 0
    html_playwright_failure_status_codes: dict[str, int] = Field(default_factory=dict)
    html_http_attempts: int = 0
    html_http_successes: int = 0
    html_http_failures: int = 0
    html_http_fallback_successes: int = 0
    html_http_fallback_failures: int = 0
    sitemap_http_attempts: int = 0
    sitemap_http_successes: int = 0
    sitemap_http_failures: int = 0
