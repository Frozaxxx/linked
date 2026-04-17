from __future__ import annotations

from app.models import SearchTarget


def test_search_target_distinguishes_exact_and_canonical_url_matches() -> None:
    target = SearchTarget(
        url="https://example.com/deep/target",
        title=None,
        text=None,
        canonical_url="https://example.com/target",
        equivalent_urls=("https://example.com/target",),
    )

    assert target.url_match_reason("https://example.com/deep/target") == "url"
    assert target.url_match_reason("https://example.com/target") == "canonical_url"
    assert target.page_matches("https://example.com/target", "", "") == ["canonical_url"]
