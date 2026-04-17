from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, AsyncIterator, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.services.fetcher.exceptions import (
    BrowserHTTPStatusError,
    BrowserNoDocumentResponseError,
    PlaywrightError,
    PlaywrightTimeoutError,
)
from app.services.fetcher.detector import detect_dynamic_html
from app.services.fetcher.models import FetchedDocument, FetchTransportStats
from app.services.fetcher.session import FetchSession
from app.services.fetcher.stealth import (
    build_browser_context_options,
    build_browser_fingerprint,
    build_init_scripts,
)
from app.settings import get_settings

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Playwright,
        Route,
        async_playwright,
    )

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    Browser = BrowserContext = Playwright = Route = Any
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
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
HTTP_TO_BROWSER_STATUS_CODES = {403, 408, 425, 429, 500, 502, 503, 504}
PARTIAL_HTML_MIN_BYTES = 1024
PARTIAL_HTML_RANGE_BYTES = 65_536


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
            trust_env=settings.fetch_trust_env,
            limits=httpx.Limits(
                max_connections=settings.fetch_max_connections,
                max_keepalive_connections=settings.fetch_max_keepalive_connections,
            ),
        )
        session = FetchSession(http_client=http_client)
        try:
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
        failure_status_callback: Callable[[int, str], None] | None = None,
        allow_partial_html: bool = False,
        prefer_partial_html: bool = False,
    ) -> FetchedDocument | None:
        if total_timeout_seconds is not None and total_timeout_seconds <= 0:
            return None

        deadline = perf_counter() + total_timeout_seconds if total_timeout_seconds is not None else None
        if render_html and settings.fetch_html_render_mode == "browser-only":
            return await self._fetch_html_with_browser(
                session,
                url,
                deadline=deadline,
                failure_status_callback=failure_status_callback,
            )

        session.record_fetch_mode(render_html=render_html, mode="http-only")
        if render_html:
            session.fetch_stats.html_http_attempts += 1
        else:
            session.fetch_stats.sitemap_http_attempts += 1
        try:
            document = await self._retry_fetch(
                lambda timeout_seconds: self._fetch_with_http(
                    session.http_client,
                    url,
                    timeout_seconds=timeout_seconds,
                    allow_partial_html=render_html and allow_partial_html,
                    prefer_partial_html=prefer_partial_html,
                    fetch_stats=session.fetch_stats,
                ),
                deadline=deadline,
            )
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
            self._notify_failure_status(failure_status_callback, exc)
            if render_html:
                session.fetch_stats.html_http_failures += 1
                self._record_http_failure(session.fetch_stats, exc, render_html=True)
                if self._should_retry_html_with_browser(session, exc=exc):
                    browser_document = await self._fetch_html_with_browser(
                        session,
                        url,
                        deadline=deadline,
                        failure_status_callback=failure_status_callback,
                    )
                    if browser_document is not None:
                        return browser_document
            else:
                session.fetch_stats.sitemap_http_failures += 1
                self._record_http_failure(session.fetch_stats, exc, render_html=False)
            logger.info("HTTP fetch failed for %s after retries: %s: %s", url, type(exc).__name__, exc)
            await self._save_debug_error(url=url, stage="http", exc=exc)
            return None
        if document is None:
            if render_html:
                session.fetch_stats.html_http_failures += 1
            else:
                session.fetch_stats.sitemap_http_failures += 1
            return None
        if render_html:
            session.fetch_stats.html_http_successes += 1
            if document.partial:
                session.fetch_stats.html_http_partial_successes += 1
            if self._should_render_http_document_with_browser(session, document):
                browser_document = await self._fetch_html_with_browser(
                    session,
                    url,
                    deadline=deadline,
                    failure_status_callback=failure_status_callback,
                )
                if browser_document is not None:
                    return browser_document
        else:
            session.fetch_stats.sitemap_http_successes += 1
        return document

    async def _fetch_html_with_browser(
        self,
        session: FetchSession,
        url: str,
        *,
        deadline: float | None,
        failure_status_callback: Callable[[int, str], None] | None,
    ) -> FetchedDocument | None:
        if not self._browser_can_be_attempted(session):
            return None

        session.record_fetch_mode(render_html=True, mode="playwright")
        session.fetch_stats.html_playwright_attempts += 1
        try:
            browser_context = await self._resolve_browser_context(session)
        except RuntimeError as exc:
            session.fetch_stats.html_playwright_failures += 1
            session.fetch_stats.html_playwright_other_failures += 1
            session.browser_unavailable = True
            logger.warning("Playwright browser session is unavailable, falling back to HTTP-only fetching: %s", exc)
            return None

        if browser_context is None:
            return await self._fetch_html_with_ephemeral_browser(
                session,
                url,
                deadline=deadline,
                failure_status_callback=failure_status_callback,
            )

        try:
            document = await self._retry_fetch(
                lambda timeout_seconds: self._fetch_with_browser(
                    browser_context,
                    url,
                    timeout_seconds=timeout_seconds,
                ),
                deadline=deadline,
                retry_count=0,
            )
        except (BrowserHTTPStatusError, PlaywrightError, PlaywrightTimeoutError) as exc:
            session.fetch_stats.html_playwright_failures += 1
            self._record_playwright_failure(session.fetch_stats, exc)
            self._notify_failure_status(failure_status_callback, exc)
            logger.debug("Browser fetch failed for %s after retries: %s", url, exc)
            return None

        if document is not None:
            session.fetch_stats.html_playwright_successes += 1
            if document.partial:
                session.fetch_stats.html_playwright_partial_successes += 1
            return document

        session.fetch_stats.html_playwright_failures += 1
        session.fetch_stats.html_playwright_other_failures += 1
        return None

    async def _resolve_browser_context(self, session: FetchSession) -> BrowserContext | None:
        if settings.fetch_browser_ws_endpoint:
            return None

        if session.browser_context is not None:
            return session.browser_context

        browser_session = await self._create_browser_session(session.http_client)
        session.playwright = browser_session.playwright
        session.browser = browser_session.browser
        session.browser_context = browser_session.browser_context
        session.fetch_stats.playwright_session_available = True
        return session.browser_context

    async def _fetch_html_with_ephemeral_browser(
        self,
        session: FetchSession,
        url: str,
        *,
        deadline: float | None,
        failure_status_callback: Callable[[int, str], None] | None,
    ) -> FetchedDocument | None:
        browser_session: FetchSession | None = None
        try:
            browser_session = await self._create_browser_session(session.http_client)
            session.fetch_stats.playwright_session_available = True
            document = await self._retry_fetch(
                lambda timeout_seconds: self._fetch_with_browser(
                    browser_session.browser_context,
                    url,
                    timeout_seconds=timeout_seconds,
                ),
                deadline=deadline,
                retry_count=0,
            )
        except (BrowserHTTPStatusError, PlaywrightError, PlaywrightTimeoutError) as exc:
            session.fetch_stats.html_playwright_failures += 1
            self._record_playwright_failure(session.fetch_stats, exc)
            self._notify_failure_status(failure_status_callback, exc)
            logger.debug("Browser fetch failed for %s after retries: %s", url, exc)
            return None
        except RuntimeError as exc:
            session.fetch_stats.html_playwright_failures += 1
            session.fetch_stats.html_playwright_other_failures += 1
            session.browser_unavailable = True
            logger.warning("Playwright browser session is unavailable, falling back to HTTP-only fetching: %s", exc)
            return None
        finally:
            if browser_session is not None:
                await self._close_browser_artifacts(
                    browser_context=browser_session.browser_context,
                    browser=browser_session.browser,
                    playwright=browser_session.playwright,
                )

        if document is not None:
            session.fetch_stats.html_playwright_successes += 1
            if document.partial:
                session.fetch_stats.html_playwright_partial_successes += 1
            return document

        session.fetch_stats.html_playwright_failures += 1
        session.fetch_stats.html_playwright_other_failures += 1
        return None

    async def _retry_fetch(
        self,
        fetch_operation,
        *,
        deadline: float | None,
        retry_count: int | None = None,
    ) -> FetchedDocument | None:
        resolved_retry_count = self._retry_count if retry_count is None else retry_count
        retryer = AsyncRetrying(
            stop=stop_after_attempt(resolved_retry_count + 1),
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

    async def _create_browser_session(
        self,
        http_client: httpx.AsyncClient,
        *,
        playwright: Playwright | None = None,
    ) -> FetchSession:
        if playwright is None and (not PLAYWRIGHT_AVAILABLE or async_playwright is None):
            raise RuntimeError(
                "Playwright Python package is required for HTML fetching. Install the project dependencies. "
                "If FETCH_BROWSER_WS_ENDPOINT is not configured, also run "
                f"'playwright install {settings.fetch_browser_name}'."
            )
        owns_playwright = playwright is None
        browser: Browser | None = None
        browser_context: BrowserContext | None = None
        try:
            if playwright is None:
                playwright = await async_playwright().start()
            browser_factory = getattr(playwright, settings.fetch_browser_name, None)
            if browser_factory is None:
                raise ValueError(f"Unsupported browser type: {settings.fetch_browser_name}")
            if settings.fetch_browser_ws_endpoint:
                if settings.fetch_browser_name != "chromium":
                    raise ValueError("Remote Browserless CDP endpoint is supported only for chromium")
                browser = await browser_factory.connect_over_cdp(
                    self._build_browser_ws_endpoint(settings.fetch_browser_ws_endpoint)
                )
            else:
                browser = await browser_factory.launch(headless=settings.fetch_browser_headless)
            fingerprint = build_browser_fingerprint()
            browser_context = await browser.new_context(**build_browser_context_options(fingerprint))
            for script in build_init_scripts(fingerprint):
                await browser_context.add_init_script(script)
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
                playwright=playwright if owns_playwright else None,
            )
            if settings.fetch_browser_ws_endpoint:
                raise RuntimeError(
                    "Failed to connect to remote Browserless/Chromium endpoint. "
                    "Verify that Docker is running, browserless is healthy, "
                    "FETCH_BROWSER_WS_ENDPOINT points to ws://localhost:3000, and FETCH_BROWSER_TOKEN matches."
                ) from exc
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
        allow_partial_html: bool = False,
        prefer_partial_html: bool = False,
        fetch_stats: FetchTransportStats | None = None,
    ) -> FetchedDocument | None:
        body_bytes = bytearray()
        response: httpx.Response | None = None
        max_html_bytes = settings.fetch_html_max_bytes if allow_partial_html else 0
        if prefer_partial_html and max_html_bytes > 0:
            range_fetch_bytes = min(
                max_html_bytes,
                max(settings.fetch_html_early_return_bytes, PARTIAL_HTML_MIN_BYTES),
            )
            if fetch_stats is not None:
                fetch_stats.html_http_range_attempts += 1
            range_document = await self._fetch_partial_html_range(
                client,
                url,
                timeout_seconds=timeout_seconds,
                max_bytes=range_fetch_bytes,
            )
            if range_document is not None:
                if fetch_stats is not None:
                    fetch_stats.html_http_range_successes += 1
                return range_document
            if fetch_stats is not None:
                fetch_stats.html_http_range_failures += 1
            return None

        try:
            async with client.stream("GET", url, timeout=httpx.Timeout(timeout_seconds)) as streamed_response:
                response = streamed_response
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if prefer_partial_html and max_html_bytes > 0 and len(body_bytes) + len(chunk) >= max_html_bytes:
                        remaining_bytes = max(max_html_bytes - len(body_bytes), 0)
                        capped_body_bytes = bytearray(body_bytes)
                        if remaining_bytes:
                            capped_body_bytes.extend(chunk[:remaining_bytes])
                        if self._can_return_partial_html(
                            response=response,
                            body_bytes=capped_body_bytes,
                            allow_partial_html=allow_partial_html,
                        ):
                            logger.info(
                                "Returning partial HTML after reaching fetch size limit: "
                                "url=%s final_url=%s bytes=%s limit=%s content_length=%s",
                                url,
                                response.url,
                                len(capped_body_bytes),
                                max_html_bytes,
                                response.headers.get("content-length"),
                            )
                            return self._build_fetched_document(
                                requested_url=url,
                                final_url=str(response.url),
                                body_bytes=bytes(capped_body_bytes),
                                content_type=response.headers.get("content-type", ""),
                                partial=True,
                                encoding=response.encoding,
                            )
                    body_bytes.extend(chunk)
        except httpx.TimeoutException:
            if response is not None and self._can_return_partial_html(
                response=response,
                body_bytes=body_bytes,
                allow_partial_html=allow_partial_html,
            ):
                logger.info(
                    "Returning partial HTML after HTTP read timeout: url=%s final_url=%s bytes=%s content_length=%s",
                    url,
                    response.url,
                    len(body_bytes),
                    response.headers.get("content-length"),
                )
                return self._build_fetched_document(
                    requested_url=url,
                    final_url=str(response.url),
                    body_bytes=bytes(body_bytes),
                    content_type=response.headers.get("content-type", ""),
                    partial=True,
                    encoding=response.encoding,
                )
            raise
        except httpx.RequestError:
            if allow_partial_html:
                if fetch_stats is not None:
                    fetch_stats.html_http_range_attempts += 1
                range_document = await self._fetch_partial_html_range(
                    client,
                    url,
                    timeout_seconds=timeout_seconds,
                    max_bytes=(
                        min(
                            max_html_bytes,
                            max(settings.fetch_html_early_return_bytes, PARTIAL_HTML_MIN_BYTES),
                        )
                        if max_html_bytes > 0
                        else PARTIAL_HTML_RANGE_BYTES
                    ),
                )
                if range_document is not None:
                    if fetch_stats is not None:
                        fetch_stats.html_http_range_successes += 1
                    return range_document
                if fetch_stats is not None:
                    fetch_stats.html_http_range_failures += 1
            raise
        if response is None:
            raise httpx.RequestError("HTTP response was not created", request=httpx.Request("GET", url))
        return FetchedDocument(
            requested_url=url,
            final_url=str(response.url),
            body=self._decode_response_body(bytes(body_bytes), response.encoding),
            content_type=response.headers.get("content-type", ""),
            body_bytes=bytes(body_bytes),
        )

    async def _fetch_partial_html_range(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int = PARTIAL_HTML_RANGE_BYTES,
    ) -> FetchedDocument | None:
        body_bytes = bytearray()
        response: httpx.Response | None = None
        range_bytes = max(max_bytes, PARTIAL_HTML_MIN_BYTES)
        early_return_bytes = min(
            range_bytes,
            max(settings.fetch_html_early_return_bytes, PARTIAL_HTML_MIN_BYTES),
        )
        try:
            async with client.stream(
                "GET",
                url,
                headers={"Range": f"bytes=0-{range_bytes - 1}"},
                timeout=httpx.Timeout(timeout_seconds),
            ) as streamed_response:
                response = streamed_response
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if len(body_bytes) + len(chunk) >= range_bytes:
                        remaining_bytes = max(range_bytes - len(body_bytes), 0)
                        if remaining_bytes:
                            body_bytes.extend(chunk[:remaining_bytes])
                        break
                    body_bytes.extend(chunk)
                    if len(body_bytes) >= early_return_bytes:
                        break
        except httpx.TimeoutException as exc:
            if response is not None and self._can_return_partial_html(
                response=response,
                body_bytes=body_bytes,
                allow_partial_html=True,
            ):
                logger.info(
                    "Returning partial HTML from timed out HTTP range request: "
                    "url=%s final_url=%s bytes=%s limit=%s",
                    url,
                    response.url,
                    len(body_bytes),
                    range_bytes,
                )
                return self._build_fetched_document(
                    requested_url=url,
                    final_url=str(response.url),
                    body_bytes=bytes(body_bytes),
                    content_type=response.headers.get("content-type", ""),
                    partial=True,
                    encoding=response.encoding,
                )
            logger.debug("HTTP range fetch failed for %s: %s", url, exc)
            return None
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.debug("HTTP range fetch failed for %s: %s", url, exc)
            return None

        if response is None or not self._can_return_partial_html(
            response=response,
            body_bytes=body_bytes,
            allow_partial_html=True,
        ):
            return None

        logger.info(
            "Returning partial HTML from HTTP range request: url=%s final_url=%s bytes=%s status=%s",
            url,
            response.url,
            len(body_bytes),
            response.status_code,
        )
        return self._build_fetched_document(
            requested_url=url,
            final_url=str(response.url),
            body_bytes=bytes(body_bytes),
            content_type=response.headers.get("content-type", ""),
            partial=True,
            encoding=response.encoding,
        )

    def _should_retry_html_with_browser(self, session: FetchSession, *, exc: BaseException) -> bool:
        if settings.fetch_html_render_mode == "http-only":
            return False
        if not self._browser_can_be_attempted(session):
            return False
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in HTTP_TO_BROWSER_STATUS_CODES
        return isinstance(exc, (httpx.TimeoutException, httpx.RequestError))

    def _should_render_http_document_with_browser(self, session: FetchSession, document: FetchedDocument) -> bool:
        if settings.fetch_html_render_mode != "auto":
            return False
        if not self._browser_can_be_attempted(session):
            return False
        if not self._is_html_document(document):
            return False
        if document.partial:
            return False
        detection = detect_dynamic_html(document.body, content_type=document.content_type)
        if detection.should_render:
            logger.info(
                "HTTP HTML looks dynamic, switching to Playwright: url=%s reasons=%s text_len=%s anchors=%s scripts=%s",
                document.final_url,
                ",".join(detection.reasons),
                detection.visible_text_length,
                detection.anchor_count,
                detection.script_count,
            )
        return detection.should_render

    @staticmethod
    def _is_html_document(document: FetchedDocument) -> bool:
        content_type = document.content_type.lower()
        return not content_type or "html" in content_type

    @staticmethod
    def _can_return_partial_html(
        *,
        response: httpx.Response,
        body_bytes: bytearray,
        allow_partial_html: bool,
    ) -> bool:
        if not allow_partial_html:
            return False
        content_type = response.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type:
            return False
        return len(body_bytes) >= PARTIAL_HTML_MIN_BYTES

    @classmethod
    def _build_fetched_document(
        cls,
        *,
        requested_url: str,
        final_url: str,
        body_bytes: bytes,
        content_type: str,
        partial: bool,
        encoding: str | None,
    ) -> FetchedDocument:
        return FetchedDocument(
            requested_url=requested_url,
            final_url=final_url,
            body=cls._decode_response_body(body_bytes, encoding),
            content_type=content_type,
            body_bytes=body_bytes,
            partial=partial,
        )

    @staticmethod
    def _decode_response_body(body_bytes: bytes, encoding: str | None) -> str:
        try:
            return body_bytes.decode(encoding or "utf-8", errors="replace")
        except LookupError:
            return body_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def _browser_can_be_attempted(session: FetchSession) -> bool:
        if session.browser_unavailable:
            logger.info("Skipping Playwright HTML fetch because the browser session is unavailable.")
            return False
        if not settings.fetch_browser_enabled and session.browser_context is None:
            return False
        threshold = settings.fetch_browser_timeout_disable_threshold
        if threshold > 0 and session.fetch_stats.html_playwright_timeout_failures >= threshold:
            logger.info(
                "Skipping Playwright HTML fetch because timeout circuit breaker is open: threshold=%s timeouts=%s",
                threshold,
                session.fetch_stats.html_playwright_timeout_failures,
            )
            return False
        status_threshold = settings.fetch_browser_status_disable_threshold
        if (
            status_threshold > 0
            and session.fetch_stats.html_playwright_http_status_failures >= status_threshold
        ):
            logger.info(
                "Skipping Playwright HTML fetch because HTTP status circuit breaker is open: "
                "threshold=%s status_failures=%s status_codes=%s",
                status_threshold,
                session.fetch_stats.html_playwright_http_status_failures,
                session.fetch_stats.html_playwright_failure_status_codes,
            )
            return False
        return True

    @staticmethod
    def _build_browser_ws_endpoint(endpoint: str) -> str:
        if not settings.fetch_browser_token:
            return endpoint
        parsed = urlsplit(endpoint)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("token", settings.fetch_browser_token)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

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
                raise BrowserNoDocumentResponseError(url)
            if response.status >= 400:
                raise BrowserHTTPStatusError(response.status, response.url)
            remaining_timeout_seconds = max(timeout_seconds - (perf_counter() - started_at), 0.0)
            await self._settle_page(page, timeout_seconds=remaining_timeout_seconds)
            body = await page.content()
            return FetchedDocument(
                requested_url=url,
                final_url=page.url,
                body=body,
                content_type=response.headers.get("content-type", ""),
                body_bytes=body.encode("utf-8"),
            )
        except PlaywrightTimeoutError:
            document = await self._build_partial_browser_document(page, requested_url=url)
            if document is not None:
                logger.info(
                    "Returning partial Playwright HTML after navigation timeout: url=%s final_url=%s bytes=%s",
                    url,
                    document.final_url,
                    len(document.body_bytes or b""),
                )
                return document
            await self._save_browser_debug_artifacts(page=page, url=url, stage="browser-timeout")
            raise
        except Exception as exc:
            await self._save_browser_debug_artifacts(page=page, url=url, stage="browser-error")
            await self._save_debug_error(url=url, stage="browser", exc=exc)
            raise
        finally:
            await page.close()

    async def _build_partial_browser_document(self, page: Any, *, requested_url: str) -> FetchedDocument | None:
        try:
            body = await page.content()
        except PlaywrightError:
            return None
        if len(body.encode("utf-8")) < PARTIAL_HTML_MIN_BYTES:
            return None
        return FetchedDocument(
            requested_url=requested_url,
            final_url=page.url,
            body=body,
            content_type="text/html",
            body_bytes=body.encode("utf-8"),
            partial=True,
        )

    async def _save_browser_debug_artifacts(self, *, page: Any, url: str, stage: str) -> None:
        if not settings.fetch_debug_artifacts_enabled:
            return
        artifact_dir = self._debug_artifact_dir(url=url, stage=stage)
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            try:
                html = await page.content()
                (artifact_dir / "page.html").write_text(html, encoding="utf-8")
            except Exception as exc:
                (artifact_dir / "html_error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
            try:
                await page.screenshot(path=str(artifact_dir / "screenshot.png"), full_page=True)
            except Exception as exc:
                (artifact_dir / "screenshot_error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        except Exception:
            logger.exception("Failed to save browser debug artifacts for %s", url)

    async def _save_debug_error(self, *, url: str, stage: str, exc: BaseException) -> None:
        if not settings.fetch_debug_artifacts_enabled:
            return
        artifact_dir = self._debug_artifact_dir(url=url, stage=stage)
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        except Exception:
            logger.exception("Failed to save debug error for %s", url)

    @staticmethod
    def _debug_artifact_dir(*, url: str, stage: str) -> Path:
        parsed = urlsplit(url)
        slug_source = f"{parsed.netloc}{parsed.path or '/'}"
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug_source).strip("-")[:120] or "page"
        return Path(settings.fetch_debug_artifacts_dir) / stage / slug

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
    def _record_playwright_failure(stats: FetchTransportStats, exc: BaseException) -> None:
        if isinstance(exc, PlaywrightTimeoutError):
            stats.html_playwright_timeout_failures += 1
            return
        if isinstance(exc, BrowserHTTPStatusError):
            stats.html_playwright_http_status_failures += 1
            status_key = str(exc.status_code)
            stats.html_playwright_failure_status_codes[status_key] = (
                stats.html_playwright_failure_status_codes.get(status_key, 0) + 1
            )
            return
        if isinstance(exc, BrowserNoDocumentResponseError):
            stats.html_playwright_no_response_failures += 1
            return
        stats.html_playwright_other_failures += 1

    @staticmethod
    def _record_http_failure(stats: FetchTransportStats, exc: BaseException, *, render_html: bool) -> None:
        if isinstance(exc, httpx.TimeoutException):
            if render_html:
                stats.html_http_timeout_failures += 1
            else:
                stats.sitemap_http_timeout_failures += 1
            return

        if isinstance(exc, httpx.HTTPStatusError):
            status_key = str(exc.response.status_code)
            if render_html:
                stats.html_http_status_failures += 1
                stats.html_http_failure_status_codes[status_key] = (
                    stats.html_http_failure_status_codes.get(status_key, 0) + 1
                )
            else:
                stats.sitemap_http_status_failures += 1
                stats.sitemap_http_failure_status_codes[status_key] = (
                    stats.sitemap_http_failure_status_codes.get(status_key, 0) + 1
                )
            return

        if isinstance(exc, httpx.RequestError):
            if render_html:
                stats.html_http_request_failures += 1
            else:
                stats.sitemap_http_request_failures += 1

    @staticmethod
    def _notify_failure_status(
        callback: Callable[[int, str], None] | None,
        exc: BaseException,
    ) -> None:
        if callback is None:
            return
        if isinstance(exc, BrowserHTTPStatusError):
            callback(exc.status_code, exc.url)
            return
        if isinstance(exc, httpx.HTTPStatusError):
            callback(exc.response.status_code, str(exc.response.url))

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
