from __future__ import annotations

import asyncio
from heapq import heappop, heappush
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright

from config import (
    ANALYZE_TIME_BUDGET_SECONDS,
    CRAWL_CONCURRENCY,
    CRAWL_MAX_DEPTH,
    FETCH_ACCEPT_HEADER,
    HTTP_TIMEOUT_SECONDS,
    MAX_CRAWL_LEVEL_SIZE,
    MAX_PAGES,
    OBEY_ROBOTS_TXT,
    USER_AGENT,
)
from models import CrawlStats, Page

from .browser import block_heavy_resources, create_stealth_context, open_browser
from .fetch import fetch_page, fetch_page_wait_domcontent, fetch_target_page
from .parser import fill_internal_link_counts
from .result_models import CrawlResult
from .robots import RobotsPolicy, fetch_robots, fetch_sitemap_urls
from .urls import (
    canonical_host,
    estimated_structural_depth,
    get_home_url,
    is_internal_url,
    normalize_url,
    priority_item,
    target_parent_urls,
    url_priority,
    urls_equal,
)


async def crawl_site(target_url: str) -> CrawlResult:
    deadline = perf_counter() + ANALYZE_TIME_BUDGET_SECONDS
    target_url = normalize_url(target_url, allow_ignored_extensions=True) or target_url
    home_url = get_home_url(target_url)
    allowed_host = canonical_host(urlsplit(home_url).hostname)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": FETCH_ACCEPT_HEADER},
        trust_env=False,
    ) as client:
        robots = await fetch_robots(client, home_url, allowed_host)
        sitemap_urls = await fetch_sitemap_urls(client, home_url, allowed_host, robots)

    found_in_sitemap = target_url in sitemap_urls
    parent_urls = target_parent_urls(target_url, allowed_host)
    discovered = {home_url, *sitemap_urls, *parent_urls}
    stats = CrawlStats(discovered=len(discovered))
    pages: dict[str, Page] = {}
    found = False
    steps_to_target: int | None = None
    found_path: list[str] = []

    async with async_playwright() as playwright:
        browser = await open_browser(playwright)
        try:
            context = await create_stealth_context(browser)
            await context.route("**/*", block_heavy_resources)

            target_fetch = await fetch_target_page(
                context,
                target_url,
                allowed_host,
                depth=None,
                path=[target_url],
                stats=stats,
            )
            target_page = target_fetch.page
            target_status = target_fetch.status
            target_error = target_fetch.error
            if target_page is not None:
                for link in target_page.links:
                    if not is_internal_url(link.url, allowed_host):
                        continue
                    if OBEY_ROBOTS_TXT and not robots.is_allowed(link.url):
                        continue
                    discovered.add(link.url)
                stats.discovered = len(discovered)
            if target_fetch.is_unavailable:
                return CrawlResult(
                    home_url=home_url,
                    target_url=target_fetch.final_url or target_url,
                    target_page=None,
                    target_status=target_fetch.status,
                    target_error=target_fetch.error,
                    pages=pages,
                    discovered_urls=discovered,
                    stats=stats,
                    found=False,
                    steps_to_target=None,
                    path=[],
                    robots_checked=True,
                    robots_available=robots.available,
                    sitemap_checked=True,
                    found_in_sitemap=found_in_sitemap,
                )

            queue: list[tuple[int, int, int, str, list[str]]] = []
            queued = {home_url}
            heappush(queue, priority_item(home_url, target_url, depth=0, path=[home_url]))

            while queue and len(pages) < MAX_PAGES and not deadline_expired(deadline):
                batch = pop_crawl_batch(queue, pages, robots, limit=CRAWL_CONCURRENCY)
                if not batch:
                    break
                results = await asyncio.gather(
                    *[
                        fetch_page(context, url, allowed_host, depth=depth, path=path, stats=stats)
                        for depth, url, path in batch
                    ],
                    return_exceptions=True,
                )

                for (depth, _url, path), result in zip(batch, results):
                    if isinstance(result, Exception) or result is None:
                        continue
                    page = result
                    if depth == 0 and not page.links:
                        retry_page = await fetch_page_wait_domcontent(
                            context,
                            page.url,
                            allowed_host,
                            depth=depth,
                            path=path,
                            stats=stats,
                        )
                        if retry_page is not None and retry_page.links:
                            page = retry_page
                    pages[page.url] = page

                    if urls_equal(page.url, target_url):
                        found = True
                        steps_to_target = depth
                        found_path = path
                        break

                    if depth >= CRAWL_MAX_DEPTH:
                        continue

                    for link in sorted(page.links, key=lambda item: url_priority(item.url, target_url)):
                        if not is_internal_url(link.url, allowed_host):
                            continue
                        if OBEY_ROBOTS_TXT and not robots.is_allowed(link.url):
                            continue
                        discovered.add(link.url)
                        next_path = path + [link.url]
                        if urls_equal(link.url, target_url):
                            found = True
                            steps_to_target = depth + 1
                            found_path = next_path
                            break
                        if link.url not in queued and len(queue) < MAX_CRAWL_LEVEL_SIZE:
                            queued.add(link.url)
                            heappush(queue, priority_item(link.url, target_url, depth=depth + 1, path=next_path))
                    stats.discovered = len(discovered)
                    if found:
                        break
                if found:
                    break

            for parent_url in parent_urls:
                if len(pages) >= MAX_PAGES or deadline_expired(deadline):
                    break
                if parent_url in pages or urls_equal(parent_url, home_url):
                    continue
                if OBEY_ROBOTS_TXT and not robots.is_allowed(parent_url):
                    continue
                page = await fetch_page(
                    context,
                    parent_url,
                    allowed_host,
                    depth=estimated_structural_depth(parent_url),
                    path=[parent_url],
                    stats=stats,
                )
                if page is not None:
                    pages[page.url] = page

            await fetch_extra_pages(
                context=context,
                urls=sorted(sitemap_urls, key=lambda item: url_priority(item, target_url)),
                allowed_host=allowed_host,
                robots=robots,
                pages=pages,
                skipped_urls=queued | {target_url},
                deadline=deadline,
                stats=stats,
            )
        finally:
            await browser.close()

    fill_internal_link_counts(pages)

    return CrawlResult(
        home_url=home_url,
        target_url=target_url,
        target_page=target_page,
        target_status=target_status,
        target_error=target_error,
        pages=pages,
        discovered_urls=discovered,
        stats=stats,
        found=found,
        steps_to_target=steps_to_target,
        path=found_path,
        robots_checked=True,
        robots_available=robots.available,
        sitemap_checked=True,
        found_in_sitemap=found_in_sitemap,
    )

async def fetch_extra_pages(
    *,
    context,
    urls: list[str],
    allowed_host: str,
    robots: RobotsPolicy,
    pages: dict[str, Page],
    skipped_urls: set[str],
    deadline: float,
    stats: CrawlStats,
) -> None:
    batch: list[str] = []
    for url in urls:
        if len(pages) + len(batch) >= MAX_PAGES or deadline_expired(deadline):
            break
        if url in pages or url in skipped_urls:
            continue
        if OBEY_ROBOTS_TXT and not robots.is_allowed(url):
            continue
        batch.append(url)
        if len(batch) >= CRAWL_CONCURRENCY:
            await fetch_and_store_batch(context, batch, allowed_host, pages, stats)
            batch = []

    if batch and len(pages) < MAX_PAGES and not deadline_expired(deadline):
        await fetch_and_store_batch(context, batch, allowed_host, pages, stats)

async def fetch_and_store_batch(
    context,
    urls: list[str],
    allowed_host: str,
    pages: dict[str, Page],
    stats: CrawlStats,
) -> None:
    results = await asyncio.gather(
        *[
            fetch_page(
                context,
                url,
                allowed_host,
                depth=estimated_structural_depth(url),
                path=[url],
                stats=stats,
            )
            for url in urls
        ],
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Page):
            pages[result.url] = result

def pop_crawl_batch(
    queue: list[tuple[int, int, int, str, list[str]]],
    pages: dict[str, Page],
    robots: RobotsPolicy,
    *,
    limit: int,
) -> list[tuple[int, str, list[str]]]:
    batch: list[tuple[int, str, list[str]]] = []
    while queue and len(batch) < limit:
        depth, _path_score, _branch_score, url, path = heappop(queue)
        if depth > CRAWL_MAX_DEPTH or url in pages:
            continue
        if OBEY_ROBOTS_TXT and not robots.is_allowed(url):
            continue
        batch.append((depth, url, path))
    return batch

def deadline_expired(deadline: float) -> bool:
    return perf_counter() >= deadline

