from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import httpx
import pytest

from app.settings import get_settings
from app.services.fetcher import (
    AsyncFetcher,
    BrowserHTTPStatusError,
    FetchSession,
    FetchedDocument,
    PlaywrightTimeoutError,
)


class TimeoutAfterChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunk: bytes) -> None:
        self._chunk = chunk

    async def __aiter__(self):
        yield self._chunk
        raise httpx.ReadTimeout("stalled while reading response body")


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@pytest.fixture(autouse=True)
def reset_fetch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "fetch_browser_enabled", False)
    monkeypatch.setattr(settings, "fetch_html_render_mode", "auto")
    monkeypatch.setattr(settings, "fetch_browser_ws_endpoint", None)
    monkeypatch.setattr(settings, "fetch_browser_token", None)
    monkeypatch.setattr(settings, "fetch_html_max_bytes", 65_536)
    monkeypatch.setattr(settings, "fetch_html_early_return_bytes", 16_384)
    monkeypatch.setattr(settings, "fetch_browser_stealth_enabled", True)
    monkeypatch.setattr(settings, "fetch_browser_randomize_fingerprint", False)
    monkeypatch.setattr(settings, "fetch_browser_ignore_https_errors", True)
    monkeypatch.setattr(settings, "fetch_browser_timeout_disable_threshold", 2)
    monkeypatch.setattr(settings, "fetch_browser_status_disable_threshold", 2)


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
async def test_fetch_prefers_http_for_html_when_browser_context_exists(
    browser_session: FetchSession,
) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><a href='/about'>About</a></body></html>",
            content_type="text/html",
        )
    )
    fetcher._fetch_with_browser = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html>rendered</html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert browser_session.html_fetch_mode == "http-only"
    assert browser_session.fetch_stats.playwright_session_available is True
    assert browser_session.fetch_stats.html_playwright_attempts == 0
    assert browser_session.fetch_stats.html_playwright_successes == 0
    assert browser_session.fetch_stats.html_playwright_failures == 0
    assert browser_session.fetch_stats.html_http_attempts == 1
    assert browser_session.fetch_stats.html_http_successes == 1


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
async def test_fetch_returns_partial_html_when_response_body_stalls() -> None:
    partial_html = b"<html><head><title>Slow</title></head><body>" + (b"x" * 5000)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8", "content-length": "90000"},
            stream=TimeoutAfterChunkStream(partial_html),
            request=request,
        )

    transport = httpx.MockTransport(handler)
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0, transport=transport)

    async with fetcher.create_client() as session:
        document = await fetcher.fetch(
            session,
            "https://example.com/slow-page",
            total_timeout_seconds=1,
            allow_partial_html=True,
        )

        assert document is not None
        assert document.partial is True
        assert "Slow" in document.body
        assert session.fetch_stats.html_http_attempts == 1
        assert session.fetch_stats.html_http_successes == 1
        assert session.fetch_stats.html_http_partial_successes == 1
        assert session.fetch_stats.html_http_failures == 0
        assert session.fetch_stats.html_http_timeout_failures == 0


@pytest.mark.asyncio
async def test_fetch_does_not_return_partial_html_by_default_when_response_body_stalls() -> None:
    partial_html = b"<html><head><title>Slow</title></head><body>" + (b"x" * 5000)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8", "content-length": "90000"},
            stream=TimeoutAfterChunkStream(partial_html),
            request=request,
        )

    transport = httpx.MockTransport(handler)
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0, transport=transport)

    async with fetcher.create_client() as session:
        document = await fetcher.fetch(
            session,
            "https://example.com/slow-page",
            total_timeout_seconds=1,
        )

        assert document is None
        assert session.fetch_stats.html_http_attempts == 1
        assert session.fetch_stats.html_http_successes == 0
        assert session.fetch_stats.html_http_partial_successes == 0
        assert session.fetch_stats.html_http_failures == 1
        assert session.fetch_stats.html_http_timeout_failures == 1


@pytest.mark.asyncio
async def test_fetch_returns_partial_html_when_size_limit_is_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "fetch_html_max_bytes", 4096)
    chunks = [
        b"<html><head><title>Large</title></head><body>",
        b"x" * 3000,
        b"y" * 3000,
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8", "content-length": "90000"},
            stream=ChunkedStream(chunks),
            request=request,
        )

    transport = httpx.MockTransport(handler)
    fetcher = AsyncFetcher(timeout_seconds=10, retry_count=0, transport=transport)

    async with fetcher.create_client() as session:
        document = await fetcher.fetch(
            session,
            "https://example.com/large-page",
            total_timeout_seconds=10,
            allow_partial_html=True,
            prefer_partial_html=True,
        )

        assert document is not None
        assert document.partial is True
        assert len(document.body_bytes or b"") == 4096
        assert "Large" in document.body
        assert session.fetch_stats.html_http_attempts == 1
        assert session.fetch_stats.html_http_successes == 1
        assert session.fetch_stats.html_http_partial_successes == 1
        assert session.fetch_stats.html_http_timeout_failures == 0


@pytest.mark.asyncio
async def test_fetch_can_skip_preferred_partial_html_range() -> None:
    seen_ranges: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_ranges.append(request.headers.get("range"))
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body><a href='/full'>Full</a></body></html>",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0, transport=transport)

    async with fetcher.create_client() as session:
        document = await fetcher.fetch(
            session,
            "https://example.com/",
            total_timeout_seconds=1,
            prefer_partial_html=False,
        )

        assert document is not None
        assert document.partial is False
        assert seen_ranges == [None]
        assert session.fetch_stats.html_http_range_attempts == 0


@pytest.mark.asyncio
async def test_fetch_uses_range_request_when_initial_html_request_fails() -> None:
    partial_html = b"<html><head><title>Range</title></head><body>" + (b"x" * 1500)
    seen_ranges: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        range_header = request.headers.get("range")
        seen_ranges.append(range_header)
        if range_header is None:
            raise httpx.ConnectError("connection closed before response", request=request)
        return httpx.Response(
            206,
            headers={"content-type": "text/html; charset=utf-8", "content-range": "bytes 0-65535/90000"},
            content=partial_html,
            request=request,
        )

    transport = httpx.MockTransport(handler)
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0, transport=transport)

    async with fetcher.create_client() as session:
        document = await fetcher.fetch(
            session,
            "https://example.com/flaky-page",
            total_timeout_seconds=1,
            allow_partial_html=True,
        )

        assert document is not None
        assert document.partial is True
        assert "Range" in document.body
        assert seen_ranges == [None, "bytes=0-16383"]
        assert session.fetch_stats.html_http_attempts == 1
        assert session.fetch_stats.html_http_successes == 1
        assert session.fetch_stats.html_http_partial_successes == 1
        assert session.fetch_stats.html_http_range_attempts == 1
        assert session.fetch_stats.html_http_range_successes == 1
        assert session.fetch_stats.html_http_failures == 0


@pytest.mark.asyncio
async def test_fetch_does_not_return_partial_sitemap_when_response_body_stalls() -> None:
    partial_xml = b"<?xml version='1.0'?><urlset>" + (b"x" * 5000)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/xml", "content-length": "90000"},
            stream=TimeoutAfterChunkStream(partial_xml),
            request=request,
        )

    transport = httpx.MockTransport(handler)
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0, transport=transport)

    async with fetcher.create_client() as session:
        document = await fetcher.fetch(
            session,
            "https://example.com/sitemap.xml",
            render_html=False,
            total_timeout_seconds=1,
        )

        assert document is None
        assert session.fetch_stats.sitemap_http_attempts == 1
        assert session.fetch_stats.sitemap_http_failures == 1
        assert session.fetch_stats.sitemap_http_timeout_failures == 1


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
async def test_fetch_uses_playwright_when_http_document_looks_dynamic(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><div id=\"root\"></div><script src=\"/app.js\"></script></body></html>",
            content_type="text/html",
        )
    )
    fetcher._fetch_with_browser = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><a href='/rendered'>Rendered</a></body></html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert document.body == "<html><body><a href='/rendered'>Rendered</a></body></html>"
    assert browser_session.html_fetch_mode == "http-to-playwright"
    assert fetcher._fetch_with_http.await_count == 1
    assert fetcher._fetch_with_browser.await_count == 1
    assert browser_session.fetch_stats.html_playwright_attempts == 1
    assert browser_session.fetch_stats.html_playwright_successes == 1
    assert browser_session.fetch_stats.html_playwright_failures == 0
    assert browser_session.fetch_stats.html_http_attempts == 1
    assert browser_session.fetch_stats.html_http_successes == 1


@pytest.mark.asyncio
async def test_fetch_does_not_render_partial_http_document_with_playwright(
    browser_session: FetchSession,
) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><div id=\"root\"></div><script src=\"/app.js\"></script></body></html>",
            content_type="text/html",
            partial=True,
        )
    )
    fetcher._fetch_with_browser = AsyncMock()

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert document.partial is True
    assert fetcher._fetch_with_browser.await_count == 0
    assert browser_session.html_fetch_mode == "http-only"
    assert browser_session.fetch_stats.html_playwright_attempts == 0
    assert browser_session.fetch_stats.html_http_successes == 1


@pytest.mark.asyncio
async def test_fetch_uses_playwright_when_http_request_is_blocked(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    request = httpx.Request("GET", "https://example.com/")
    response = httpx.Response(403, request=request)
    fetcher._fetch_with_http = AsyncMock(side_effect=httpx.HTTPStatusError("forbidden", request=request, response=response))
    fetcher._fetch_with_browser = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html>rendered</html>",
            content_type="text/html",
        )
    )
    recorded_statuses: list[tuple[int, str]] = []

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
        failure_status_callback=lambda status, url: recorded_statuses.append((status, url)),
    )

    assert document is not None
    assert document.body == "<html>rendered</html>"
    assert browser_session.html_fetch_mode == "http-to-playwright"
    assert browser_session.fetch_stats.html_http_failures == 1
    assert browser_session.fetch_stats.html_http_status_failures == 1
    assert browser_session.fetch_stats.html_http_failure_status_codes == {"403": 1}
    assert browser_session.fetch_stats.html_playwright_successes == 1
    assert recorded_statuses == [(403, "https://example.com/")]


@pytest.mark.asyncio
async def test_fetch_uses_playwright_when_http_request_times_out(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._fetch_with_http = AsyncMock(side_effect=httpx.ReadTimeout("body stalled"))
    fetcher._fetch_with_browser = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html>rendered after timeout</html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert document.body == "<html>rendered after timeout</html>"
    assert browser_session.html_fetch_mode == "http-to-playwright"
    assert browser_session.fetch_stats.html_http_failures == 1
    assert browser_session.fetch_stats.html_http_timeout_failures == 1
    assert browser_session.fetch_stats.html_playwright_attempts == 1
    assert browser_session.fetch_stats.html_playwright_successes == 1


@pytest.mark.asyncio
async def test_fetch_does_not_retry_playwright_after_dynamic_http_response(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=2)
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><div id=\"app\"></div><script src=\"/app.js\"></script></body></html>",
            content_type="text/html",
        )
    )
    fetcher._fetch_with_browser = AsyncMock(side_effect=PlaywrightTimeoutError("browser timed out"))

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert document.body == "<html><body><div id=\"app\"></div><script src=\"/app.js\"></script></body></html>"
    assert fetcher._fetch_with_http.await_count == 1
    assert fetcher._fetch_with_browser.await_count == 1
    assert browser_session.fetch_stats.html_playwright_timeout_failures == 1
    assert browser_session.fetch_stats.html_http_successes == 1


@pytest.mark.asyncio
async def test_fetch_skips_playwright_after_repeated_timeouts(browser_session: FetchSession) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    browser_session.fetch_stats.html_playwright_timeout_failures = 2
    fetcher._fetch_with_browser = AsyncMock()
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><div id=\"app\"></div><script src=\"/app.js\"></script></body></html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert fetcher._fetch_with_browser.await_count == 0
    assert fetcher._fetch_with_http.await_count == 1
    assert browser_session.html_fetch_mode == "http-only"
    assert browser_session.fetch_stats.html_http_fallback_successes == 0


@pytest.mark.asyncio
async def test_fetch_skips_playwright_after_repeated_browser_status_failures(
    browser_session: FetchSession,
) -> None:
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    browser_session.fetch_stats.html_playwright_http_status_failures = 2
    browser_session.fetch_stats.html_playwright_failure_status_codes = {"403": 2}
    fetcher._fetch_with_browser = AsyncMock()
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><div id=\"app\"></div><script src=\"/app.js\"></script></body></html>",
            content_type="text/html",
        )
    )

    document = await fetcher.fetch(
        browser_session,
        "https://example.com/",
        total_timeout_seconds=0.5,
    )

    assert document is not None
    assert fetcher._fetch_with_browser.await_count == 0
    assert fetcher._fetch_with_http.await_count == 1
    assert browser_session.html_fetch_mode == "http-only"


@pytest.mark.asyncio
async def test_fetch_skips_playwright_after_browser_startup_failure(
    http_session: FetchSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "fetch_browser_enabled", True)
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    fetcher._create_browser_session = AsyncMock(side_effect=RuntimeError("browser unavailable"))
    fetcher._fetch_with_http = AsyncMock(
        return_value=FetchedDocument(
            requested_url="https://example.com/",
            final_url="https://example.com/",
            body="<html><body><div id=\"app\"></div><script src=\"/app.js\"></script></body></html>",
            content_type="text/html",
        )
    )

    first_document = await fetcher.fetch(
        http_session,
        "https://example.com/first",
        total_timeout_seconds=0.5,
    )
    second_document = await fetcher.fetch(
        http_session,
        "https://example.com/second",
        total_timeout_seconds=0.5,
    )

    assert first_document is not None
    assert second_document is not None
    assert http_session.browser_unavailable is True
    assert fetcher._create_browser_session.await_count == 1
    assert http_session.fetch_stats.html_playwright_attempts == 1
    assert http_session.fetch_stats.html_playwright_failures == 1
    assert fetcher._fetch_with_http.await_count == 2


@pytest.mark.asyncio
async def test_fetch_with_browser_returns_partial_dom_after_navigation_timeout() -> None:
    page = MagicMock()
    page.url = "https://example.com/slow-browser-page"
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("navigation timed out"))
    page.content = AsyncMock(return_value="<html><head><title>Browser</title></head><body>" + ("x" * 5000))
    page.close = AsyncMock()
    browser_context = AsyncMock()
    browser_context.new_page = AsyncMock(return_value=page)
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)

    document = await fetcher._fetch_with_browser(
        browser_context,
        "https://example.com/slow-browser-page",
        timeout_seconds=1,
    )

    assert document.partial is True
    assert "Browser" in document.body
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_records_playwright_status_failures(
    browser_session: FetchSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "fetch_html_render_mode", "browser-only")
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
async def test_create_client_falls_back_to_http_when_browser_session_does_not_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "fetch_browser_enabled", True)
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
    assert kwargs["screen"] == {"width": 1366, "height": 768}
    assert kwargs["timezone_id"] == "America/New_York"
    assert kwargs["ignore_https_errors"] is True
    assert kwargs["extra_http_headers"]["Sec-Fetch-Mode"] == "navigate"
    assert kwargs["extra_http_headers"]["Upgrade-Insecure-Requests"] == "1"
    assert browser_context.add_init_script.await_count == 3


@pytest.mark.asyncio
async def test_create_browser_session_can_connect_to_browserless(
    http_session: FetchSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "fetch_browser_ws_endpoint", "ws://browserless:3000")
    monkeypatch.setattr(settings, "fetch_browser_token", "secret")
    fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
    browser_context = AsyncMock()
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=browser_context)
    browser_factory = MagicMock()
    browser_factory.connect_over_cdp = AsyncMock(return_value=browser)
    browser_factory.launch = AsyncMock()
    playwright = AsyncMock()
    playwright.chromium = browser_factory

    session = await fetcher._create_browser_session(http_session.http_client, playwright=playwright)

    assert session.browser_context is browser_context
    browser_factory.connect_over_cdp.assert_awaited_once_with("ws://browserless:3000?token=secret")
    browser_factory.launch.assert_not_called()
