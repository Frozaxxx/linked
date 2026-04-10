from __future__ import annotations

import gzip
import logging
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
ROBOTS_LINE_RE = re.compile(r"^\s*([^:#\s][^:]*)\s*:\s*(.*?)\s*$")
logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class ParsedRobotsTxt:
    allow_rules: tuple[str, ...]
    disallow_rules: tuple[str, ...]
    sitemap_urls: list[str]

    def is_allowed(self, url: str) -> bool:
        path = _robots_url_path(url)
        allow_match_length = _best_robots_match_length(path, self.allow_rules)
        disallow_match_length = _best_robots_match_length(path, self.disallow_rules)
        if disallow_match_length < 0:
            return True
        return allow_match_length >= disallow_match_length


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
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None

    if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
        return None

    host = hostname.casefold()
    if port and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
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


def parse_sitemap(xml_body: str | bytes, allowed_host: str) -> ParsedSitemap:
    payload = _prepare_sitemap_payload(xml_body)
    if payload is None:
        return ParsedSitemap(page_urls=[], nested_sitemaps=[])
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        logger.warning("Failed to parse sitemap XML: %s", exc)
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


def _prepare_sitemap_payload(xml_body: str | bytes) -> str | bytes | None:
    if isinstance(xml_body, str):
        return xml_body
    if xml_body.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(xml_body)
        except (OSError, EOFError) as exc:
            logger.warning("Failed to decompress gzip sitemap: %s", exc)
            return None
    return xml_body


def parse_robots_txt(xml_body: str, base_url: str, allowed_host: str, user_agent: str) -> ParsedRobotsTxt:
    groups: list[dict[str, list[str]]] = []
    current_group = {"user_agents": [], "allow": [], "disallow": []}
    sitemaps: list[str] = []

    def flush_group() -> None:
        if current_group["user_agents"] or current_group["allow"] or current_group["disallow"]:
            groups.append(
                {
                    "user_agents": list(current_group["user_agents"]),
                    "allow": list(current_group["allow"]),
                    "disallow": list(current_group["disallow"]),
                }
            )
            current_group["user_agents"].clear()
            current_group["allow"].clear()
            current_group["disallow"].clear()

    for raw_line in xml_body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            flush_group()
            continue

        match = ROBOTS_LINE_RE.match(line)
        if match is None:
            continue

        field_name = match.group(1).strip().casefold()
        value = match.group(2).strip()
        if field_name == "user-agent":
            if current_group["allow"] or current_group["disallow"]:
                flush_group()
            if value:
                current_group["user_agents"].append(value.casefold())
            continue

        if field_name == "allow":
            if current_group["user_agents"] and value:
                current_group["allow"].append(_normalize_robots_rule(value))
            continue

        if field_name == "disallow":
            if current_group["user_agents"] and value:
                current_group["disallow"].append(_normalize_robots_rule(value))
            continue

        if field_name == "sitemap" and value:
            normalized = normalize_url(value, base_url, allow_ignored_extensions=True)
            if normalized and is_internal_url(normalized, allowed_host) and normalized not in sitemaps:
                sitemaps.append(normalized)

    flush_group()

    matched_groups = _select_robots_groups(groups, user_agent=user_agent)
    allow_rules: list[str] = []
    disallow_rules: list[str] = []
    for group in matched_groups:
        allow_rules.extend(group["allow"])
        disallow_rules.extend(group["disallow"])

    return ParsedRobotsTxt(
        allow_rules=tuple(allow_rules),
        disallow_rules=tuple(disallow_rules),
        sitemap_urls=sitemaps,
    )


def _select_robots_groups(groups: list[dict[str, list[str]]], *, user_agent: str) -> list[dict[str, list[str]]]:
    normalized_user_agent = user_agent.strip().casefold()
    exact_matches: list[dict[str, list[str]]] = []
    wildcard_matches: list[dict[str, list[str]]] = []

    for group in groups:
        user_agents = group["user_agents"]
        if any(agent != "*" and agent and agent in normalized_user_agent for agent in user_agents):
            exact_matches.append(group)
            continue
        if "*" in user_agents:
            wildcard_matches.append(group)

    if exact_matches:
        return exact_matches
    return wildcard_matches


def _normalize_robots_rule(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme or parsed.netloc:
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    return value.strip() or "/"


def _robots_url_path(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _best_robots_match_length(path: str, rules: tuple[str, ...]) -> int:
    best_length = -1
    for rule in rules:
        if _robots_rule_matches(path, rule):
            best_length = max(best_length, len(rule))
    return best_length


def _robots_rule_matches(path: str, rule: str) -> bool:
    if not rule:
        return False

    anchored = rule.endswith("$")
    escaped_rule = re.escape(rule[:-1] if anchored else rule)
    pattern = escaped_rule.replace(r"\*", ".*")
    if anchored:
        return re.match(rf"^{pattern}$", path) is not None
    return re.match(rf"^{pattern}", path) is not None
