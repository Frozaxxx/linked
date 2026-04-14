from __future__ import annotations

from time import perf_counter

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
