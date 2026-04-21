from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from models import Link, Page

from .urls import is_internal_url, normalize_url


def parse_page(html: str, url: str, allowed_host: str, *, depth: int | None, path: list[str]) -> Page:
    soup = BeautifulSoup(html, "lxml")
    title = extract_title(soup)
    h1 = extract_heading(soup)
    breadcrumbs = extract_breadcrumbs(soup)
    snippet = extract_snippet(soup)
    links: list[Link] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = normalize_url(anchor.get("href"), url)
        if not href or href in seen or not is_internal_url(href, allowed_host):
            continue
        seen.add(href)
        links.append(Link(url=href, anchor=anchor.get_text(separator=" ", strip=True)))

    return Page(
        url=url,
        title=title,
        h1=h1,
        text=snippet,
        breadcrumbs=breadcrumbs,
        parent_section=parent_section_from_url(url),
        links=links,
        depth=depth,
        path=path,
    )

def extract_title(soup: BeautifulSoup) -> str:
    for selector in (
        "title",
        "meta[property='og:title']",
        "meta[name='twitter:title']",
    ):
        node = soup.select_one(selector)
        if node is None:
            continue
        value = node.get("content") if node.name == "meta" else node.get_text(separator=" ", strip=True)
        cleaned = clean_text(str(value or ""))
        if cleaned:
            return cleaned
    return ""

def extract_heading(soup: BeautifulSoup) -> str:
    for selector in ("h1", "main h2", "article h2", "h2"):
        node = soup.select_one(selector)
        cleaned = clean_text(text_of(node)) if node is not None else ""
        if cleaned:
            return cleaned
    return ""

def extract_snippet(soup: BeautifulSoup) -> str:
    for element in soup(["script", "style", "noscript", "template", "svg"]):
        element.decompose()
    source = soup.select_one("main") or soup.select_one("article") or soup.body
    if source is None:
        return ""
    return clean_text(source.get_text(separator=" ", strip=True))[:300]

def has_text_feature(page: Page) -> bool:
    return bool(page.url and (page.title or page.h1 or page.breadcrumbs or page.text))

def extract_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    selectors = [
        "[aria-label='breadcrumb'] a",
        "[aria-label='Breadcrumb'] a",
        ".breadcrumb a",
        ".breadcrumbs a",
        "nav.breadcrumb a",
    ]
    values: list[str] = []
    for selector in selectors:
        for element in soup.select(selector):
            value = clean_text(element.get_text(separator=" ", strip=True))
            if value and value not in values:
                values.append(value)
        if values:
            break
    return values[:6]

def parent_section_from_url(url: str) -> str:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    if len(parts) <= 1:
        return ""
    return parts[-2].replace("-", " ").replace("_", " ")

def fill_internal_link_counts(pages: dict[str, Page]) -> None:
    counts = {url: 0 for url in pages}
    for page in pages.values():
        for link in page.links:
            normalized = normalize_url(link.url)
            if normalized in counts:
                counts[normalized] += 1
    for url, count in counts.items():
        pages[url].internal_link_count = count

def text_of(node) -> str:
    return node.get_text(separator=" ", strip=True) if node is not None else ""

def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()

