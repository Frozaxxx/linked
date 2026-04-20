from __future__ import annotations

from time import perf_counter

import httpx
import pytest
from pydantic import ValidationError

from app.schemas import LinkingAnalyzeRequest
from app.services.internal_linking import InternalLinkingAnalyzer
from app.models import SitemapSnapshot
from app.settings import get_settings


def test_target_url_only_request_derives_start_url_from_site_root() -> None:
    request = LinkingAnalyzeRequest(target_url="https://example.com/catalog/target-page")
    analyzer = InternalLinkingAnalyzer(request)

    assert analyzer._start_url == "https://example.com/"
    assert analyzer._requested_target_url == "https://example.com/catalog/target-page"


def test_branch_urls_get_priority_over_unrelated_urls() -> None:
    request = LinkingAnalyzeRequest(
        target_url=(
            "https://example.com/regional-collaboration-network/regions-great-lakes/"
            "glri/about-glri/glri-focus-area-5-foundations/target-page"
        )
    )
    analyzer = InternalLinkingAnalyzer(request)

    branch_score = analyzer._score_discovered_link(
        "https://example.com/regional-collaboration-network/regions-great-lakes/glri",
        "",
    )
    unrelated_score = analyzer._score_discovered_link(
        "https://example.com/news-release",
        "",
    )

    assert branch_score > unrelated_score


def test_target_related_sitemaps_are_checked_before_unrelated_sitemaps() -> None:
    request = LinkingAnalyzeRequest(
        target_url="https://www.rbc.ru/economics/2019/12/20/5dfc5a679a7947d1b5b3e8a9",
    )
    analyzer = InternalLinkingAnalyzer(request)
    sitemap_queue = [
        "https://www.rbc.ru/sitemaps/sport/2026/04.xml",
        "https://www.rbc.ru/economics/2019/12/sitemap.xml",
        "https://www.rbc.ru/sitemaps/news/2026/04.xml",
    ]

    analyzer._prioritize_sitemap_queue(sitemap_queue, checked=set())

    assert sitemap_queue[0] == "https://www.rbc.ru/economics/2019/12/sitemap.xml"


def test_sitemap_uses_separate_time_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "sitemap_time_budget_seconds", 5.0)
    request = LinkingAnalyzeRequest(target_url="https://example.com/catalog/target-page")
    analyzer = InternalLinkingAnalyzer(request)
    sitemap = SitemapSnapshot(started_at=perf_counter() - 4.0)

    remaining = analyzer._remaining_sitemap_budget_seconds(sitemap)

    assert 0 < remaining <= 1.5


def test_403_on_non_target_branch_blocks_sibling_urls() -> None:
    request = LinkingAnalyzeRequest(
        target_url="https://example.com/regional-collaboration-network/regions-great-lakes/target-page",
    )
    analyzer = InternalLinkingAnalyzer(request)

    analyzer._record_html_fetch_failure_status(403, "https://example.com/news-release/story-a")

    assert analyzer._is_html_403_branch_blocked("https://example.com/news-release/story-b")
    assert not analyzer._is_html_403_branch_blocked(
        "https://example.com/regional-collaboration-network/regions-great-lakes"
    )


@pytest.mark.asyncio
async def test_sitemap_fallback_frontier_starts_crawl_when_homepage_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "request_retry_count", 0)
    monkeypatch.setattr(settings, "fetch_browser_enabled", False)
    monkeypatch.setattr(settings, "yandex_gpt_enabled", False)
    monkeypatch.setattr(settings, "sitemap_time_budget_seconds", 5.0)

    target_url = "https://www.rbc.ru/economics/2019/12/20/5dfc5a679a7947d1b5b3e8a9"
    source_url = "https://www.rbc.ru/economics/2019/12/20/source"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.rbc.ru/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nAllow: /\nSitemap: https://www.rbc.ru/sitemap_index.xml\n",
            )
        if url == "https://www.rbc.ru/sitemap_index.xml":
            return httpx.Response(
                200,
                text=(
                    "<?xml version='1.0' encoding='UTF-8'?>"
                    "<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                    "<sitemap><loc>https://www.rbc.ru/economics/2019/12/sitemap.xml</loc></sitemap>"
                    "</sitemapindex>"
                ),
                headers={"content-type": "application/xml"},
            )
        if url == "https://www.rbc.ru/economics/2019/12/sitemap.xml":
            return httpx.Response(
                200,
                text=(
                    "<?xml version='1.0' encoding='UTF-8'?>"
                    "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                    f"<url><loc>{target_url}</loc></url>"
                    f"<url><loc>{source_url}</loc></url>"
                    "</urlset>"
                ),
                headers={"content-type": "application/xml"},
            )
        if url == source_url:
            return httpx.Response(
                200,
                text=f"<html><body><a href='{target_url}'>target</a></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404, text="not found")

    analyzer = InternalLinkingAnalyzer(
        LinkingAnalyzeRequest(target_url=target_url),
        transport=httpx.MockTransport(handler),
    )

    response = await analyzer.analyze()

    assert response.found is True
    assert response.found_in_sitemap is True
    assert response.pages_fetched >= 1
    assert response.path == [source_url, target_url]


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("start_url", "https://example.com/"),
        ("target_title", "Target page"),
        ("target_text", "Target text"),
        ("timeout_seconds", 5),
        ("retry_count", 0),
    ],
)
def test_request_rejects_any_input_except_target_url(field_name: str, field_value: object) -> None:
    with pytest.raises(ValidationError):
        LinkingAnalyzeRequest(
            target_url="https://example.com/catalog/target-page",
            **{field_name: field_value},
        )
