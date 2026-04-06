from __future__ import annotations

import httpx
import unittest
from unittest.mock import AsyncMock

from app.services.fetcher import AsyncFetcher, FetchSession, FetchedDocument


class AsyncFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_returns_none_when_total_timeout_is_exhausted(self) -> None:
        fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
        session = FetchSession(http_client=httpx.AsyncClient())
        self.addAsyncCleanup(session.http_client.aclose)

        document = await fetcher.fetch(
            session,
            "https://example.com/",
            total_timeout_seconds=0,
        )

        self.assertIsNone(document)

    async def test_fetch_caps_attempt_timeout_by_total_timeout(self) -> None:
        fetcher = AsyncFetcher(timeout_seconds=10, retry_count=0)
        session = FetchSession(http_client=httpx.AsyncClient())
        self.addAsyncCleanup(session.http_client.aclose)
        fetcher._fetch_with_http = AsyncMock(
            return_value=FetchedDocument(
                requested_url="https://example.com/",
                final_url="https://example.com/",
                body="<html></html>",
                content_type="text/html",
            )
        )

        document = await fetcher.fetch(
            session,
            "https://example.com/",
            total_timeout_seconds=0.5,
        )

        self.assertIsNotNone(document)
        _, kwargs = fetcher._fetch_with_http.await_args
        self.assertLessEqual(kwargs["timeout_seconds"], 0.5)
        self.assertGreater(kwargs["timeout_seconds"], 0)

    async def test_fetch_marks_html_mode_as_playwright_when_browser_context_exists(self) -> None:
        fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
        session = FetchSession(http_client=httpx.AsyncClient(), browser_context=object())
        self.addAsyncCleanup(session.http_client.aclose)
        fetcher._fetch_with_browser = AsyncMock(
            return_value=FetchedDocument(
                requested_url="https://example.com/",
                final_url="https://example.com/",
                body="<html></html>",
                content_type="text/html",
            )
        )

        document = await fetcher.fetch(
            session,
            "https://example.com/",
            total_timeout_seconds=0.5,
        )

        self.assertIsNotNone(document)
        self.assertEqual(session.html_fetch_mode, "playwright")

    async def test_fetch_marks_html_mode_as_http_only_without_browser_context(self) -> None:
        fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
        session = FetchSession(http_client=httpx.AsyncClient())
        self.addAsyncCleanup(session.http_client.aclose)
        fetcher._fetch_with_http = AsyncMock(
            return_value=FetchedDocument(
                requested_url="https://example.com/",
                final_url="https://example.com/",
                body="<html></html>",
                content_type="text/html",
            )
        )

        document = await fetcher.fetch(
            session,
            "https://example.com/",
            total_timeout_seconds=0.5,
        )

        self.assertIsNotNone(document)
        self.assertEqual(session.html_fetch_mode, "http-only")

    async def test_fetch_marks_sitemap_mode_as_http_only(self) -> None:
        fetcher = AsyncFetcher(timeout_seconds=1, retry_count=0)
        session = FetchSession(http_client=httpx.AsyncClient(), browser_context=object())
        self.addAsyncCleanup(session.http_client.aclose)
        fetcher._fetch_with_http = AsyncMock(
            return_value=FetchedDocument(
                requested_url="https://example.com/sitemap.xml",
                final_url="https://example.com/sitemap.xml",
                body="<?xml version='1.0' encoding='UTF-8'?><urlset/>",
                content_type="application/xml",
            )
        )

        document = await fetcher.fetch(
            session,
            "https://example.com/sitemap.xml",
            render_html=False,
            total_timeout_seconds=0.5,
        )

        self.assertIsNotNone(document)
        self.assertEqual(session.sitemap_fetch_mode, "http-only")


if __name__ == "__main__":
    unittest.main()
