from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Any, AsyncIterator

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.settings import get_settings

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Error as PlaywrightError,
        Playwright,
        Route,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    Browser = BrowserContext = Playwright = Route = Any

    class PlaywrightError(Exception):
        pass

    class PlaywrightTimeoutError(PlaywrightError):
        pass

    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False


settings = get_settings()
logger = logging.getLogger(__name__)


DEFAULT_HEADERS = {
    "User-Agent": settings.fetch_user_agent,
    "Accept": settings.fetch_accept_header,
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
BROWSER_HEADERS = {key: value for key, value in DEFAULT_HEADERS.items() if key != "User-Agent"}
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


@dataclass(slots=True)
class FetchedDocument:
    requested_url: str
    final_url: str
    body: str
    content_type: str


@dataclass(slots=True)
class FetchSession:
    http_client: httpx.AsyncClient
    playwright: Playwright | None = None
    browser: Browser | None = None
    browser_context: BrowserContext | None = None
    html_fetch_mode: str = "not-requested"
    sitemap_fetch_mode: str = "not-requested"

    def record_fetch_mode(self, *, render_html: bool, mode: str) -> None:
        if render_html:
            self.html_fetch_mode = self._merge_fetch_mode(self.html_fetch_mode, mode)
            return
        self.sitemap_fetch_mode = self._merge_fetch_mode(self.sitemap_fetch_mode, mode)

    @staticmethod
    def _merge_fetch_mode(current: str, new: str) -> str:
        if current == "not-requested":
            return new
        if current == new:
            return current
        return "mixed"

    async def close(self) -> None:
        try:
            if self.browser_context is not None:
                await self.browser_context.close()
        finally:
            try:
                if self.browser is not None:
                    await self.browser.close()
            finally:
                try:
                    if self.playwright is not None:
                        await self.playwright.stop()
                finally:
                    await self.http_client.aclose()


class BrowserHTTPStatusError(Exception):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"Browser fetch failed with status {status_code} for {url}")


class AsyncFetcher:
    def __init__(
        self,
        timeout_seconds: float,
        retry_count: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._retry_count = retry_count
        self._transport = transport

    @asynccontextmanager
    async def create_client(self) -> AsyncIterator[FetchSession]:
        http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
            headers=DEFAULT_HEADERS,
        )
        session = FetchSession(http_client=http_client)
        try:
            try:
                session = await self._create_browser_session(http_client)
            except RuntimeError as exc:
                logger.warning("Playwright browser session is unavailable, falling back to HTTP-only fetching: %s", exc)
            yield session
        finally:
            await session.close()

    async def fetch(
        self,
        session: FetchSession,
        url: str,
        *,
        render_html: bool = True,
        total_timeout_seconds: float | None = None,
    ) -> FetchedDocument | None:
        if total_timeout_seconds is not None and total_timeout_seconds <= 0:
            return None

        deadline = perf_counter() + total_timeout_seconds if total_timeout_seconds is not None else None
        if render_html and session.browser_context is not None:
            session.record_fetch_mode(render_html=True, mode="playwright")
            try:
                return await self._retry_fetch(
                    lambda timeout_seconds: self._fetch_with_browser(
                        session.browser_context,
                        url,
                        timeout_seconds=timeout_seconds,
                    ),
                    deadline=deadline,
                )
            except (BrowserHTTPStatusError, PlaywrightError, PlaywrightTimeoutError) as exc:
                logger.debug("Browser fetch failed for %s after retries, falling back to HTTP: %s", url, exc)

        session.record_fetch_mode(render_html=render_html, mode="http-only")
        try:
            return await self._retry_fetch(
                lambda timeout_seconds: self._fetch_with_http(
                    session.http_client,
                    url,
                    timeout_seconds=timeout_seconds,
                ),
                deadline=deadline,
            )
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.debug("HTTP fetch failed for %s after retries: %s", url, exc)
            return None

    async def _retry_fetch(
        self,
        fetch_operation,
        *,
        deadline: float | None,
    ) -> FetchedDocument | None:
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self._retry_count + 1),
            wait=wait_exponential(multiplier=0.3, min=0.3, max=2),
            retry=retry_if_exception(self._is_retryable_exception),
            reraise=True,
        )

        async for attempt in retryer:
            attempt_timeout_seconds = self._resolve_attempt_timeout_seconds(deadline)
            if attempt_timeout_seconds is None:
                return None
            with attempt:
                return await fetch_operation(attempt_timeout_seconds)

        return None

    async def _create_browser_session(self, http_client: httpx.AsyncClient) -> FetchSession:
        if not PLAYWRIGHT_AVAILABLE or async_playwright is None:
            raise RuntimeError(
                "Playwright is required for HTML fetching. Install the package dependencies and run "
                f"'playwright install {settings.fetch_browser_name}'."
            )
        playwright: Playwright | None = None
        browser: Browser | None = None
        browser_context: BrowserContext | None = None
        try:
            playwright = await async_playwright().start()
            browser_factory = getattr(playwright, settings.fetch_browser_name, None)
            if browser_factory is None:
                raise ValueError(f"Unsupported browser type: {settings.fetch_browser_name}")
            browser = await browser_factory.launch(headless=settings.fetch_browser_headless)
            browser_context = await browser.new_context(
                user_agent=settings.fetch_user_agent,
                locale="en-US",
                extra_http_headers=BROWSER_HEADERS,
            )
            await browser_context.route("**/*", self._handle_route)
            return FetchSession(
                http_client=http_client,
                playwright=playwright,
                browser=browser,
                browser_context=browser_context,
            )
        except Exception as exc:
            await self._close_browser_artifacts(
                browser_context=browser_context,
                browser=browser,
                playwright=playwright,
            )
            raise RuntimeError(
                f"Failed to start Playwright browser '{settings.fetch_browser_name}'. "
                f"Run 'playwright install {settings.fetch_browser_name}' and verify the runtime can launch the browser."
            ) from exc

    def _resolve_attempt_timeout_seconds(self, deadline: float | None) -> float | None:
        if deadline is None:
            return self._timeout

        remaining_seconds = deadline - perf_counter()
        if remaining_seconds <= 0:
            return None
        return min(self._timeout, remaining_seconds)

    async def _fetch_with_http(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        timeout_seconds: float,
    ) -> FetchedDocument:
        response = await client.get(url, timeout=httpx.Timeout(timeout_seconds))
        response.raise_for_status()
        return FetchedDocument(
            requested_url=url,
            final_url=str(response.url),
            body=response.text,
            content_type=response.headers.get("content-type", ""),
        )

    async def _fetch_with_browser(
        self,
        browser_context: BrowserContext,
        url: str,
        *,
        timeout_seconds: float,
    ) -> FetchedDocument:
        page = await browser_context.new_page()
        timeout_ms = max(int(timeout_seconds * 1000), 1)
        page.set_default_navigation_timeout(timeout_ms)
        started_at = perf_counter()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if response is None:
                raise PlaywrightError(f"No document response received for {url}")
            if response.status >= 400:
                raise BrowserHTTPStatusError(response.status, response.url)
            remaining_timeout_seconds = max(timeout_seconds - (perf_counter() - started_at), 0.0)
            await self._settle_page(page, timeout_seconds=remaining_timeout_seconds)
            return FetchedDocument(
                requested_url=url,
                final_url=page.url,
                body=await page.content(),
                content_type=response.headers.get("content-type", ""),
            )
        finally:
            await page.close()

    async def _settle_page(self, page: Any, *, timeout_seconds: float) -> None:
        remaining_timeout_ms = max(int(timeout_seconds * 1000), 0)

        if settings.fetch_browser_network_idle_timeout_ms > 0 and remaining_timeout_ms > 0:
            wait_started_at = perf_counter()
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=min(settings.fetch_browser_network_idle_timeout_ms, remaining_timeout_ms),
                )
            except PlaywrightTimeoutError:
                pass
            remaining_timeout_ms = max(
                remaining_timeout_ms - int((perf_counter() - wait_started_at) * 1000),
                0,
            )

        if settings.fetch_browser_post_load_wait_ms > 0 and remaining_timeout_ms > 0:
            await page.wait_for_timeout(min(settings.fetch_browser_post_load_wait_ms, remaining_timeout_ms))

    @staticmethod
    async def _handle_route(route: Route) -> None:
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        await route.continue_()

    @staticmethod
    async def _close_browser_artifacts(
        *,
        browser_context: BrowserContext | None,
        browser: Browser | None,
        playwright: Playwright | None,
    ) -> None:
        if browser_context is not None:
            await browser_context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    @staticmethod
    def _is_retryable_exception(exc: BaseException) -> bool:
        if isinstance(exc, PlaywrightTimeoutError):
            return True

        if isinstance(exc, BrowserHTTPStatusError):
            return exc.status_code in {408, 425, 429, 500, 502, 503, 504}

        if isinstance(exc, PlaywrightError):
            return True

        if isinstance(exc, httpx.TimeoutException):
            return True

        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            return status_code in {408, 425, 429, 500, 502, 503, 504}

        if isinstance(exc, httpx.RequestError):
            return True

        return False
