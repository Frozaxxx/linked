from __future__ import annotations

import gzip
import logging
import xml.etree.ElementTree as ET

from app.services.parser.models import ParsedSitemap
from app.services.parser.urls import is_internal_url, normalize_url


logger = logging.getLogger(__name__)


def parse_sitemap(xml_body: str | bytes, allowed_host: str) -> ParsedSitemap:
    payload = prepare_sitemap_payload(xml_body)
    if payload is None:
        return ParsedSitemap()
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        logger.warning("Failed to parse sitemap XML: %s", exc)
        return ParsedSitemap()

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


def prepare_sitemap_payload(xml_body: str | bytes) -> str | bytes | None:
    if isinstance(xml_body, str):
        return xml_body
    if xml_body.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(xml_body)
        except (OSError, EOFError) as exc:
            logger.warning("Failed to decompress gzip sitemap: %s", exc)
            return None
    return xml_body
