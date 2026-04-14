from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from app.services.parser.models import ExtractedLink, ParsedPage
from app.services.parser.urls import is_internal_url, normalize_url


STRIP_TEXT_TAGS = ["head", "script", "style", "noscript", "template"]


def parse_html(html: str, page_url: str, allowed_host: str) -> ParsedPage:
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node is not None else ""
    h1_node = tree.css_first("h1")
    h1 = h1_node.text(strip=True) if h1_node is not None else ""

    canonical_url: str | None = None
    for element in tree.css("link[rel][href]"):
        rel_tokens = {
            token.casefold()
            for token in element.attributes.get("rel", "").split()
            if token.strip()
        }
        if "canonical" not in rel_tokens:
            continue
        canonical_url = normalize_url(
            element.attributes.get("href"),
            page_url,
            allow_ignored_extensions=True,
        )
        break

    noindex = False
    for element in tree.css("meta[name][content]"):
        name = element.attributes.get("name", "").strip().casefold()
        if name not in {"robots", "googlebot"}:
            continue
        directives = {
            directive.strip().casefold()
            for directive in element.attributes.get("content", "").split(",")
            if directive.strip()
        }
        if "noindex" in directives:
            noindex = True
            break

    is_indexable = not noindex and (canonical_url is None or canonical_url == page_url)

    text_tree = tree.clone()
    text_tree.strip_tags(STRIP_TEXT_TAGS)
    text_source = text_tree.body or text_tree
    text = text_source.text(separator=" ", strip=True)

    links: list[ExtractedLink] = []
    seen: set[str] = set()
    for element in tree.css("a[href]"):
        normalized = normalize_url(element.attributes.get("href"), page_url)
        if not normalized or normalized in seen or not is_internal_url(normalized, allowed_host):
            continue
        seen.add(normalized)
        links.append(
            ExtractedLink(
                url=normalized,
                anchor_text=element.text(separator=" ", strip=True),
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
