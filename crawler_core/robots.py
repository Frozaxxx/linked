from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from time import perf_counter

import httpx
from pydantic import Field

from config import (
    OBEY_ROBOTS_TXT,
    REQUEST_RETRY_COUNT,
    ROBOTS_USER_AGENT,
    SITEMAP_MAX_FILES,
    SITEMAP_MAX_URLS,
    SITEMAP_REQUEST_TIMEOUT_SECONDS,
    SITEMAP_TIME_BUDGET_SECONDS,
)
from models import SimplifiedModel

from .urls import best_rule_match, is_internal_url, normalize_url, robots_path


class RobotsPolicy(SimplifiedModel):
    available: bool = False
    allow_rules: list[str] = Field(default_factory=list)
    disallow_rules: list[str] = Field(default_factory=list)
    sitemap_urls: list[str] = Field(default_factory=list)

    def is_allowed(self, url: str) -> bool:
        path = robots_path(url)
        allow = best_rule_match(path, self.allow_rules)
        disallow = best_rule_match(path, self.disallow_rules)
        return disallow < 0 or allow >= disallow

async def fetch_robots(client: httpx.AsyncClient, home_url: str, allowed_host: str) -> RobotsPolicy:
    robots_url = normalize_url("robots.txt", home_url, allow_ignored_extensions=True)
    if not robots_url:
        return RobotsPolicy()
    try:
        response = await get_with_retries(client, robots_url)
        response.raise_for_status()
    except httpx.HTTPError:
        return RobotsPolicy()
    policy = parse_robots(response.text, home_url, allowed_host)
    policy.available = True
    return policy

async def fetch_sitemap_urls(
    client: httpx.AsyncClient,
    home_url: str,
    allowed_host: str,
    robots: RobotsPolicy,
) -> set[str]:
    deadline = perf_counter() + SITEMAP_TIME_BUDGET_SECONDS
    queue = list(robots.sitemap_urls)
    fallback = normalize_url("sitemap.xml", home_url, allow_ignored_extensions=True)
    if fallback and fallback not in queue:
        queue.append(fallback)

    checked: set[str] = set()
    page_urls: set[str] = set()
    while (
        queue
        and len(checked) < SITEMAP_MAX_FILES
        and len(page_urls) < SITEMAP_MAX_URLS
        and not deadline_expired(deadline)
    ):
        sitemap_url = queue.pop(0)
        if sitemap_url in checked:
            continue
        checked.add(sitemap_url)
        try:
            response = await client.get(sitemap_url, timeout=SITEMAP_REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        parsed_pages, nested = parse_sitemap(response.content, allowed_host)
        page_urls.update(url for url in parsed_pages if not OBEY_ROBOTS_TXT or robots.is_allowed(url))
        for nested_url in nested:
            if nested_url not in checked and nested_url not in queue:
                queue.append(nested_url)
    return set(list(page_urls)[:SITEMAP_MAX_URLS])

async def get_with_retries(client: httpx.AsyncClient, url: str) -> httpx.Response:
    last_error: httpx.HTTPError | None = None
    for _attempt in range(REQUEST_RETRY_COUNT + 1):
        try:
            return await client.get(url)
        except httpx.HTTPError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise httpx.RequestError("HTTP request failed without an exception", request=httpx.Request("GET", url))

def parse_robots(body: str, base_url: str, allowed_host: str) -> RobotsPolicy:
    policy = RobotsPolicy()
    requested_agent = ROBOTS_USER_AGENT.strip().casefold()
    active = False
    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip().casefold()
        value = value.strip()
        if name == "user-agent":
            agent = value.casefold()
            active = agent == "*" or (requested_agent != "*" and agent in requested_agent)
        elif name == "allow" and active and value:
            policy.allow_rules.append(value)
        elif name == "disallow" and active and value:
            policy.disallow_rules.append(value)
        elif name == "sitemap" and value:
            sitemap = normalize_url(value, base_url, allow_ignored_extensions=True)
            if sitemap and is_internal_url(sitemap, allowed_host) and sitemap not in policy.sitemap_urls:
                policy.sitemap_urls.append(sitemap)
    return policy

def parse_sitemap(body: bytes, allowed_host: str) -> tuple[list[str], list[str]]:
    payload = body
    if body.startswith(b"\x1f\x8b"):
        try:
            payload = gzip.decompress(body)
        except OSError:
            return [], []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return [], []
    namespace = ""
    if root.tag.startswith("{") and "}" in root.tag:
        namespace = root.tag.split("}", 1)[0] + "}"

    pages: list[str] = []
    nested: list[str] = []
    for entry in root.findall(f".//{namespace}url"):
        loc = entry.find(f"{namespace}loc")
        url = normalize_url(loc.text if loc is not None else None)
        if url and is_internal_url(url, allowed_host):
            pages.append(url)
    for entry in root.findall(f".//{namespace}sitemap"):
        loc = entry.find(f"{namespace}loc")
        url = normalize_url(loc.text if loc is not None else None, allow_ignored_extensions=True)
        if url and is_internal_url(url, allowed_host):
            nested.append(url)
    return pages, nested


def deadline_expired(deadline: float) -> bool:
    return perf_counter() >= deadline

