from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.models import CrawlNode
from app.services.fetcher import FetchSession, FetchedDocument
from app.services.internal_linking.discovery import InternalLinkingDiscoveryMixin


class DiscoveryHarness(InternalLinkingDiscoveryMixin):
    def __init__(self) -> None:
        self._allowed_host = "example.com"
        self._semaphore = asyncio.Semaphore(1)
        self.fetch_calls: list[dict] = []
        self._fetcher = SimpleNamespace(fetch=self._fake_fetch)

    async def _fake_fetch(self, client: FetchSession, url: str, **kwargs):
        self.fetch_calls.append({"url": url, **kwargs})
        links = "".join(f"<a href='/section-{index}'>Section {index}</a>" for index in range(30))
        return FetchedDocument(
            requested_url=url,
            final_url=url,
            body=f"<html><body><a href='/section'>Section</a>{links}</body></html>",
            content_type="text/html",
            body_bytes=f"<html><body><a href='/section'>Section</a>{links}</body></html>".encode("utf-8"),
            partial=bool(kwargs.get("allow_partial_html")),
        )

    def _is_allowed_by_robots(self, url: str) -> bool:
        return True

    def _is_html_403_branch_blocked(self, url: str) -> bool:
        return False

    def _remaining_fetch_budget_seconds(self) -> float | None:
        return 10.0

    def _record_html_fetch_failure_status(self, status_code: int, url: str) -> None:
        return None


@pytest.mark.asyncio
async def test_fetch_node_prefers_partial_html_for_start_page() -> None:
    harness = DiscoveryHarness()
    session = FetchSession(http_client=httpx.AsyncClient())
    try:
        node, page = await harness._fetch_node(
            session,
            CrawlNode(url="https://example.com/", depth=0, path=["https://example.com/"]),
        )
    finally:
        await session.http_client.aclose()

    assert node.url == "https://example.com/"
    assert page is not None
    assert page.links[0].url == "https://example.com/section"
    assert harness.fetch_calls[0]["allow_partial_html"] is True
    assert harness.fetch_calls[0]["prefer_partial_html"] is True
    assert harness.fetch_calls[1]["partial_html_bytes"] == 65536


@pytest.mark.asyncio
async def test_fetch_node_prefers_partial_html_for_deeper_pages() -> None:
    harness = DiscoveryHarness()
    session = FetchSession(http_client=httpx.AsyncClient())
    try:
        node, page = await harness._fetch_node(
            session,
            CrawlNode(url="https://example.com/section", depth=2, path=["https://example.com/", "https://example.com/section"]),
        )
    finally:
        await session.http_client.aclose()

    assert node.url == "https://example.com/section"
    assert page is not None
    assert harness.fetch_calls[0]["allow_partial_html"] is True
    assert harness.fetch_calls[0]["prefer_partial_html"] is True


@pytest.mark.asyncio
async def test_fetch_node_retries_full_fetch_for_partial_homepage_without_links() -> None:
    class RetryHarness(InternalLinkingDiscoveryMixin):
        def __init__(self) -> None:
            self._allowed_host = "example.com"
            self._semaphore = asyncio.Semaphore(1)
            self.fetch_calls: list[dict] = []
            self._fetcher = SimpleNamespace(fetch=self._fake_fetch)

        async def _fake_fetch(self, client: FetchSession, url: str, **kwargs):
            self.fetch_calls.append({"url": url, **kwargs})
            if len(self.fetch_calls) == 1:
                return FetchedDocument(
                    requested_url=url,
                    final_url=url,
                    body="<html><body><div>Shell</div></body></html>",
                    content_type="text/html",
                    body_bytes=b"<html><body><div>Shell</div></body></html>",
                    partial=True,
                )
            return FetchedDocument(
                requested_url=url,
                final_url=url,
                body="<html><body><a href='/economics'>Economics</a></body></html>",
                content_type="text/html",
                body_bytes=b"<html><body><a href='/economics'>Economics</a></body></html>",
                partial=False,
            )

        def _is_allowed_by_robots(self, url: str) -> bool:
            return True

        def _is_html_403_branch_blocked(self, url: str) -> bool:
            return False

        def _remaining_fetch_budget_seconds(self) -> float | None:
            return 10.0

        def _record_html_fetch_failure_status(self, status_code: int, url: str) -> None:
            return None

    harness = RetryHarness()
    session = FetchSession(http_client=httpx.AsyncClient())
    try:
        node, page = await harness._fetch_node(
            session,
            CrawlNode(url="https://example.com/", depth=0, path=["https://example.com/"]),
        )
    finally:
        await session.http_client.aclose()

    assert node.url == "https://example.com/"
    assert page is not None
    assert [link.url for link in page.links] == ["https://example.com/economics"]
    assert len(harness.fetch_calls) == 2
    assert harness.fetch_calls[0]["allow_partial_html"] is True
    assert harness.fetch_calls[1]["partial_html_bytes"] == 65536
