from __future__ import annotations

import gzip

from app.services.parser import normalize_url, parse_html, parse_robots_txt
from app.services.parser import parse_sitemap


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


def test_parse_sitemap_supports_gzip_payload() -> None:
    payload = gzip.compress(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/section/page</loc></url>
</urlset>"""
    )

    parsed = parse_sitemap(payload, "example.com")

    assert parsed.page_urls == ["https://example.com/section/page"]
    assert parsed.nested_sitemaps == []


def test_parse_html_extracts_page_metadata_and_internal_links_with_beautifulsoup() -> None:
    page = parse_html(
        """
        <html>
          <head>
            <title>Example page</title>
            <link rel="canonical" href="/canonical">
            <meta name="robots" content="index,follow">
          </head>
          <body>
            <h1>Main title</h1>
            <p>Visible text</p>
            <a href="/inside">Inside</a>
            <a href="https://external.example/page">External</a>
            <script>hidden()</script>
          </body>
        </html>
        """,
        "https://example.com/page",
        "example.com",
    )

    assert page.title == "Example page"
    assert page.h1 == "Main title"
    assert page.canonical_url == "https://example.com/canonical"
    assert page.text == "Main title Visible text Inside External"
    assert page.is_indexable is False
    assert [(link.url, link.anchor_text) for link in page.links] == [
        ("https://example.com/inside", "Inside"),
    ]
