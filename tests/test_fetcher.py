from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import httpx
import pytest

from app.services.fetcher import AsyncFetcher, BrowserHTTPStatusError, FetchSession, FetchedDocument, PlaywrightError


@pytest.fixture
async def http_session() -> FetchSession:
    session = FetchSession(http_client=httpx.AsyncClient())
    yield session
    await session.http_client.aclose()


@pytest.fixture
async def browser_session() -> FetchSession:
    session = FetchSession(http_client=httpx.AsyncClient(), browser_context=object())
    yield session
    await session.http_client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_when_total_timeout_is_exhausted(http_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)

    document = await fetcher.fetch(
        http_session,
        "https://example.com/",
        total_timeout_seconds=0,
    )

    assert document is None


@pytest.mark.asyncio
async def test_fetch_caps_attempt_timeout_by_total_timeout(http_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=10, retry_count=0)
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html></html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        http_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    _, kwargs = fetcher._fetch_with_http.await_args
    assert kwargs["timeout_seconds"] <= 0.5
    assert kwargs["timeout_seconds"] > 0


@pytest.mark.asyncio
async def test_fetch_marks_html_mode_as_playwright_when_browser_context_exists(
    browser_session: FetchSession,
) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_browser = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html></html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert browser_session.html_fetch_mode == "playwright"
    assert browser_session.fetch_stats.playwright_session_available is True
    assert browser_session.fetch_stats.html_playwright_attempts == 1
    assert browser_session.fetch_stats.html_playwright_successes == 1
    assert browser_session.fetch_stats.html_playwright_failures == 0
    assert browser_session.fetch_stats.html_http_attempts == 0


@pytest.mark.asyncio
async def test_fetch_marks_html_mode_as_http_only_without_browser_context(http_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html></html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        http_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert http_session.html_fetch_mode == "http-only"
    assert http_session.fetch_stats.playwright_session_available is False
    assert http_session.fetch_stats.html_playwright_attempts == 0
    assert http_session.fetch_stats.html_http_attempts == 1
    assert http_session.fetch_stats.html_http_successes == 1
    assert http_session.fetch_stats.html_http_failures == 0


@pytest.mark.asyncio
async def test_fetch_marks_sitemap_mode_as_http_only(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/sitemap.xml",
            final_url="https://example.com/sitemap.xml",
            body="<?xml version='1.0' encoding='UTF-8'?><urlset/>",
            content_type="application/xml",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/sitemap.xml",
        render_html=False,
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert browser_session.sitemap_fetch_mode == "http-only"
    assert browser_session.fetch_stats.sitemap_http_attempts == 1
    assert browser_session.fetch_stats.sitemap_http_successes == 1
    assert browser_session.fetch_stats.sitemap_http_failures == 0


@pytest.mark.asyncio
async def test_fetch_falls_back_to_http_when_playwright_request_fails(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_browser = AsyncMock(side_effect=PlaywrightError("browser failed"))
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html>fallback</html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert document.body == "<html>fallback</html>"
    assert browser_session.html_fetch_mode == "mixed"
    assert fetcher._fetch_with_browser.await_count == 1
    assert fetcher._fetch_with_http.await_count == 1
    assert browser_session.fetch_stats.html_playwright_attempts == 1
    assert browser_session.fetch_stats.html_playwright_successes == 0
    assert browser_session.fetch_stats.html_playwright_failures == 1
    assert browser_session.fetch_stats.html_playwright_other_failures == 1
    assert browser_session.fetch_stats.html_http_attempts == 1
    assert browser_session.fetch_stats.html_http_successes == 1
    assert browser_session.fetch_stats.html_http_fallback_successes == 1
    assert browser_session.fetch_stats.html_http_fallback_failures == 0


@pytest.mark.asyncio
async def test_fetch_records_playwright_status_failures(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_browser = AsyncMock(side_effect=BrowserHTTPStatusError(429, "https://example.com/"))
    fetcher._fetch_with_http = AsyncMock(return_value=None)
    recorded_statuses: list[tuple[int, str]] = []

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
        failure_status_callback=lambda status, url: recorded_statuses.append((status, url)),
    )

    assert document is None
    assert browser_session.fetch_stats.html_playwright_failures == 1
    assert browser_session.fetch_stats.html_playwright_http_status_failures == 1
    assert browser_session.fetch_stats.html_playwright_failure_status_codes == {"429": 1}
    assert recorded_statuses == [(429, "https://example.com/")]


@pytest.mark.asyncio
async def test_create_client_falls_back_to_http_when_browser_session_does_not_start() -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._create_browser_session = AsyncMock(side_effect=RuntimeError("playwright unavailable"))

    async with fetcher.create_client() as session:
        assert session.browser_context is None
        assert session.html_fetch_mode == "not-requested"
        assert session.fetch_stats.playwright_session_available is False


@pytest.mark.asyncio
async def test_create_browser_session_sets_context_fingerprint(http_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    browser_context = AsyncMock()
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=browser_context)
    browser_factory = MagicMock()
    browser_factory.launch = AsyncMock(return_value=browser)
    playwright = AsyncMock()
    playwright.chromium = browser_factory

    session = await fetcher._create_browser_session(http_session.http_client, playwright=playwright)

    assert session.browser_context is browser_context
    _, kwargs = browser.new_context.await_args
    assert kwargs["viewport"] == {"width": 1366, "height": 768}
    assert kwargs["timezone_id"] == "America/New_York"
