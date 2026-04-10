from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.fetcher import AsyncFetcher, FetchSession, FetchedDocument, PlaywrightError


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


@pytest.mark.asyncio
async def test_create_client_falls_back_to_http_when_browser_session_does_not_start() -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._create_browser_session = AsyncMock(side_effect=RuntimeError("playwright unavailable"))

    async with fetcher.create_client() as session:
        assert session.browser_context is None
        assert session.html_fetch_mode == "not-requested"
