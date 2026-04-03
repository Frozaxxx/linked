from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser


IGNORED_PREFIXES = ("mailto:", "tel:", "javascript:", "data:")
IGNORED_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".css",
    ".js",
    ".json",
    ".xml",
)
MULTISLASH_RE = re.compile(r"/{2,}")
STRIP_TEXT_TAGS = ["head", "script", "style", "noscript", "template"]


@dataclass(slots=True)
class ExtractedLink:
    url: str
    anchor_text: str


@dataclass(slots=True)
class ParsedPage:
    url: str
    title: str
    h1: str
    text: str
    links: list[ExtractedLink]
    is_indexable: bool
    canonical_url: str | None


@dataclass(slots=True)
class ParsedSitemap:
    page_urls: list[str]
    nested_sitemaps: list[str]


def canonical_host(host: str | None) -> str:
    if not host:
        return ""
    normalized = host.casefold()
    if normalized.startswith("www."):
        return normalized[4:]
    return normalized


def get_site_root(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def normalize_url(
    url: str | None,
    base_url: str | None = None,
    *,
    allow_ignored_extensions: bool = False,
) -> str | None:
    if not url:
        return None

    raw_url = url.strip()
    if not raw_url or raw_url.startswith("#") or raw_url.casefold().startswith(IGNORED_PREFIXES):
        return None

    resolved = urljoin(base_url, raw_url) if base_url else raw_url
    parsed = urlsplit(resolved)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None

    host = parsed.hostname.casefold()
    if parsed.port and not (
        (parsed.scheme.casefold() == "http" and parsed.port == 80)
        or (parsed.scheme.casefold() == "https" and parsed.port == 443)
    ):
        netloc = f"{host}:{parsed.port}"
    else:
        netloc = host

    path = MULTISLASH_RE.sub("/", parsed.path or "/")
    if not path.startswith("/"):
        path = f"/{path}"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    lowered_path = path.casefold()
    if not allow_ignored_extensions and any(lowered_path.endswith(extension) for extension in IGNORED_EXTENSIONS):
        return None

    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((parsed.scheme.casefold(), netloc, path, query, ""))


def is_internal_url(url: str, allowed_host: str) -> bool:
    return canonical_host(urlsplit(url).hostname) == allowed_host


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


def parse_sitemap(xml_body: str, allowed_host: str) -> ParsedSitemap:
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError:
        return ParsedSitemap(page_urls=[], nested_sitemaps=[])

    namespace = ""
    if root.tag.startswith("{") and "}" in root.tag:
        namespace = root.tag.split("}", 1)[0] + "}"

    page_urls: list[str] = []
    nested_sitemaps: list[str] = []

    for url_entry in root.findall(f".//{namespace}url"):
        loc = url_entry.find(f"{namespace}loc")
        if loc is None or not loc.text:
            continue
        normalized = normalize_url(loc.text)
        if normalized and is_internal_url(normalized, allowed_host):
            page_urls.append(normalized)

    for sitemap_entry in root.findall(f".//{namespace}sitemap"):
        loc = sitemap_entry.find(f"{namespace}loc")
        if loc is None or not loc.text:
            continue
        normalized = normalize_url(loc.text, allow_ignored_extensions=True)
        if normalized and is_internal_url(normalized, allowed_host):
            nested_sitemaps.append(normalized)

    return ParsedSitemap(page_urls=page_urls, nested_sitemaps=nested_sitemaps)
