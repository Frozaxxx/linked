from __future__ import annotations

from app.services.parser import normalize_url, parse_robots_txt


def test_normalize_url_returns_none_for_invalid_port() -> None:
    value = normalize_url("https://example.com:%20broken-port/path")

    assert value is None


def test_parse_robots_txt_uses_specific_user_agent_rules() -> None:
    robots = parse_robots_txt(
        """
        User-agent: *
        Disallow: /private
        Allow: /private/public
        Sitemap: https://example.com/sitemap.xml

        User-agent: seo-linked
        Disallow: /app
        Allow: /app/public
        """,
        "https://example.com/",
        "example.com",
        "seo-linked",
    )

    assert robots.sitemap_urls == ["https://example.com/sitemap.xml"]
    assert not robots.is_allowed("https://example.com/app")
    assert robots.is_allowed("https://example.com/app/public")
    assert robots.is_allowed("https://example.com/private")


def test_parse_robots_txt_falls_back_to_wildcard_rules() -> None:
    robots = parse_robots_txt(
        """
        User-agent: *
        Disallow: /tmp
        Allow: /tmp/public$
        """,
        "https://example.com/",
        "example.com",
        "unknown-bot",
    )

    assert not robots.is_allowed("https://example.com/tmp/file")
    assert robots.is_allowed("https://example.com/tmp/public")
