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
