from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


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
    ".css",
    ".js",
    ".json",
)


def priority_item(url: str, target_url: str, *, depth: int, path: list[str]) -> tuple[int, int, int, str, list[str]]:
    path_score, branch_score = url_priority(url, target_url)
    return depth, path_score, branch_score, url, path

def url_priority(url: str, target_url: str) -> tuple[int, int]:
    return estimated_structural_depth(url), -common_path_prefix_len(url, target_url)

def estimated_structural_depth(url: str) -> int:
    return len([part for part in urlsplit(url).path.split("/") if part])

def common_path_prefix_len(left: str, right: str) -> int:
    left_parts = [part for part in urlsplit(left).path.split("/") if part]
    right_parts = [part for part in urlsplit(right).path.split("/") if part]
    count = 0
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part != right_part:
            break
        count += 1
    return count


def target_parent_urls(target_url: str, allowed_host: str) -> list[str]:
    parsed = urlsplit(target_url)
    parts = [part for part in parsed.path.split("/") if part]
    parents: list[str] = []
    for index in range(len(parts) - 1, 0, -1):
        parent = normalize_url(urlunsplit((parsed.scheme, parsed.netloc, "/" + "/".join(parts[:index]), "", "")))
        if parent and is_internal_url(parent, allowed_host) and parent not in parents:
            parents.append(parent)
    return parents


def normalize_url(url: str | None, base_url: str | None = None, *, allow_ignored_extensions: bool = False) -> str | None:
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
    netloc = host
    if parsed.port and not ((parsed.scheme == "http" and parsed.port == 80) or (parsed.scheme == "https" and parsed.port == 443)):
        netloc = f"{host}:{parsed.port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if not allow_ignored_extensions and any(path.casefold().endswith(ext) for ext in IGNORED_EXTENSIONS):
        return None
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((parsed.scheme.casefold(), netloc, path, query, ""))

def get_home_url(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}/"

def canonical_host(host: str | None) -> str:
    normalized = (host or "").casefold()
    return normalized[4:] if normalized.startswith("www.") else normalized

def is_internal_url(url: str, allowed_host: str) -> bool:
    return canonical_host(urlsplit(url).hostname) == allowed_host

def urls_equal(left: str, right: str) -> bool:
    return normalize_url(left, allow_ignored_extensions=True) == normalize_url(right, allow_ignored_extensions=True)

def robots_path(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")

def best_rule_match(path: str, rules: list[str]) -> int:
    best = -1
    for rule in rules:
        if robots_rule_matches(path, rule):
            best = max(best, len(rule))
    return best

def robots_rule_matches(path: str, rule: str) -> bool:
    if not rule:
        return False
    anchored = rule.endswith("$")
    pattern = re.escape(rule[:-1] if anchored else rule).replace(r"\*", ".*")
    if anchored:
        return re.match(rf"^{pattern}$", path) is not None
    return re.match(rf"^{pattern}", path) is not None

