from __future__ import annotations

from typing import Any

import httpx

from app.services.fetcher.models import FetchTransportStats


try:
    from playwright.async_api import Browser, BrowserContext, Playwright
except ImportError:  # pragma: no cover
    Browser = BrowserContext = Playwright = Any


class FetchSession:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        playwright: Playwright | None = None,
        browser: Browser | None = None,
        browser_context: BrowserContext | None = None,
        html_fetch_mode: str = "not-requested",
        sitemap_fetch_mode: str = "not-requested",
        fetch_stats: FetchTransportStats | None = None,
    ) -> None:
        self.http_client = http_client
        self.playwright = playwright
        self.browser = browser
        self.browser_context = browser_context
        self.html_fetch_mode = html_fetch_mode
        self.sitemap_fetch_mode = sitemap_fetch_mode
        self.fetch_stats = fetch_stats or FetchTransportStats()
        if self.browser_context is not None:
            self.fetch_stats.playwright_session_available = True

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
