from __future__ import annotations

from bs4 import BeautifulSoup

from app.services.parser.models import ExtractedLink, ParsedPage
from app.services.parser.urls import is_internal_url, normalize_url


STRIP_TEXT_TAGS = ["head", "script", "style", "noscript", "template"]


def parse_html(html: str, page_url: str, allowed_host: str) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")
    title_node = soup.select_one("title")
    title = title_node.get_text(strip=True) if title_node is not None else ""
    h1_node = soup.select_one("h1")
    h1 = h1_node.get_text(strip=True) if h1_node is not None else ""

    canonical_url: str | None = None
    for element in soup.select("link[rel][href]"):
        rel_value = element.get("rel", [])
        if isinstance(rel_value, str):
            rel_tokens = {token.casefold() for token in rel_value.split() if token.strip()}
        else:
            rel_tokens = {str(token).casefold() for token in rel_value if str(token).strip()}
        if "canonical" not in rel_tokens:
            continue
        canonical_url = normalize_url(
            element.get("href"),
            page_url,
            allow_ignored_extensions=True,
        )
        break

    noindex = False
    for element in soup.select("meta[name][content]"):
        name = element.get("name", "").strip().casefold()
        if name not in {"robots", "googlebot"}:
            continue
        directives = {
            directive.strip().casefold()
            for directive in element.get("content", "").split(",")
            if directive.strip()
        }
        if "noindex" in directives:
            noindex = True
            break

    is_indexable = not noindex and (canonical_url is None or canonical_url == page_url)

    text_soup = BeautifulSoup(html, "lxml")
    for element in text_soup(STRIP_TEXT_TAGS):
        element.decompose()
    text_source = text_soup.body or text_soup
    text = text_source.get_text(separator=" ", strip=True)

    links: list[ExtractedLink] = []
    seen: set[str] = set()
    for element in soup.select("a[href]"):
        normalized = normalize_url(element.get("href"), page_url)
        if not normalized or normalized in seen or not is_internal_url(normalized, allowed_host):
            continue
        seen.add(normalized)
        links.append(
            ExtractedLink(
                url=normalized,
                anchor_text=element.get_text(separator=" ", strip=True),
            )
        )

    return ParsedPage(
        url=page_url,
        title=title,
        h1=h1,
        text=text,
        links=links,
        is_indexable=is_indexable,
        canonical_url=canonical_url,
    )
