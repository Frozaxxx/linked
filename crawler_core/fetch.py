from __future__ import annotations

from playwright.async_api import Page as BrowserPage
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from config import (
    PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS,
    PLAYWRIGHT_POST_LOAD_WAIT_MS,
    PLAYWRIGHT_TIMEOUT_MS,
    RENDERED_HTML_MIN_LENGTH,
    RETRYABLE_TARGET_HTTP_STATUSES,
)
from models import CrawlStats, Page

from .parser import has_text_feature, parse_page
from .result_models import TargetFetchResult
from .urls import get_home_url, is_internal_url, normalize_url


async def fetch_target_page(
    context,
    url: str,
    allowed_host: str,
    *,
    depth: int | None,
    path: list[str],
    stats: CrawlStats | None = None,
) -> TargetFetchResult:
    browser_page: BrowserPage = await context.new_page()
    browser_page.set_default_navigation_timeout(PLAYWRIGHT_TIMEOUT_MS)
    try:
        record_opened(stats)
        try:
            response = await browser_page.goto(url, wait_until="commit", timeout=PLAYWRIGHT_TIMEOUT_MS)
            if stats is not None:
                stats.goto_ok += 1
        except PlaywrightTimeoutError:
            if stats is not None:
                stats.goto_timeout += 1
            response = None
        except Exception as exc:
            if stats is not None:
                stats.goto_error += 1
            return TargetFetchResult(error="")

        status = response.status if response is not None else None
        if status in RETRYABLE_TARGET_HTTP_STATUSES:
            try:
                await browser_page.wait_for_timeout(1000)
                response = await browser_page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=PLAYWRIGHT_TIMEOUT_MS,
                    referer=get_home_url(url),
                )
                if stats is not None:
                    stats.goto_ok += 1
                status = response.status if response is not None else None
            except PlaywrightTimeoutError:
                if stats is not None:
                    stats.goto_timeout += 1
            except Exception:
                if stats is not None:
                    stats.goto_error += 1
        final_url = normalize_url(browser_page.url, allow_ignored_extensions=True) or ""
        if status is not None and status >= 400:
            return TargetFetchResult(
                status=status,
                final_url=final_url,
                error=f"target returned HTTP {status}",
            )
        if not final_url:
            final_url = normalize_url(url, allow_ignored_extensions=True) or url
        if not is_internal_url(final_url, allowed_host):
            return TargetFetchResult(status=status, final_url=final_url, error="target redirected outside the site")

        try:
            await browser_page.wait_for_load_state("domcontentloaded", timeout=PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        try:
            await browser_page.wait_for_load_state("networkidle", timeout=PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        if PLAYWRIGHT_POST_LOAD_WAIT_MS > 0:
            await browser_page.wait_for_timeout(PLAYWRIGHT_POST_LOAD_WAIT_MS)

        html = await read_rendered_html(browser_page, stats=stats)
        if not html:
            return TargetFetchResult(status=status, final_url=final_url, error="")
        page = parse_page(html, final_url, allowed_host, depth=depth, path=path[:-1] + [final_url])
        if len(page.links) < 3:
            retry_page = await parse_after_link_wait(browser_page, final_url, allowed_host, depth=depth, path=path, stats=stats)
            if retry_page is not None and (has_text_feature(retry_page) or len(retry_page.links) > len(page.links)):
                page = retry_page
        if has_text_feature(page):
            if stats is not None:
                stats.extracted_any_feature += 1
                stats.content_extracted += 1
                stats.usable += 1
            return TargetFetchResult(page=page, status=status, final_url=final_url)
        return TargetFetchResult(status=status, final_url=final_url, error="target has no useful page features")
    except Exception as exc:
        return TargetFetchResult(error=f"{type(exc).__name__}: {exc}")
    finally:
        await browser_page.close()

async def fetch_page(
    context,
    url: str,
    allowed_host: str,
    *,
    depth: int | None,
    path: list[str],
    stats: CrawlStats | None = None,
) -> Page | None:
    browser_page: BrowserPage = await context.new_page()
    browser_page.set_default_navigation_timeout(PLAYWRIGHT_TIMEOUT_MS)
    try:
        record_opened(stats)
        try:
            response = await browser_page.goto(url, wait_until="commit", timeout=PLAYWRIGHT_TIMEOUT_MS)
            if stats is not None:
                stats.goto_ok += 1
        except PlaywrightTimeoutError:
            if stats is not None:
                stats.goto_timeout += 1
            response = None
        except Exception:
            if stats is not None:
                stats.goto_error += 1
            return None

        if response is not None and response.status >= 400:
            return None
        try:
            await browser_page.wait_for_load_state("domcontentloaded", timeout=PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        try:
            await browser_page.wait_for_load_state("networkidle", timeout=PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        if PLAYWRIGHT_POST_LOAD_WAIT_MS > 0:
            await browser_page.wait_for_timeout(PLAYWRIGHT_POST_LOAD_WAIT_MS)
        final_url = normalize_url(browser_page.url, allow_ignored_extensions=True)
        if not final_url or not is_internal_url(final_url, allowed_host):
            return None
        html = await read_rendered_html(browser_page, stats=stats)
        if not html:
            return None
        page = parse_page(html, final_url, allowed_host, depth=depth, path=path[:-1] + [final_url])
        if len(page.links) < 3:
            retry_page = await parse_after_link_wait(browser_page, final_url, allowed_host, depth=depth, path=path, stats=stats)
            if retry_page is not None and (has_text_feature(retry_page) or len(retry_page.links) > len(page.links)):
                page = retry_page
        if has_text_feature(page):
            if stats is not None:
                stats.extracted_any_feature += 1
                stats.content_extracted += 1
                stats.usable += 1
            return page
        return None
    except Exception:
        return None
    finally:
        await browser_page.close()

async def fetch_page_wait_domcontent(
    context,
    url: str,
    allowed_host: str,
    *,
    depth: int | None,
    path: list[str],
    stats: CrawlStats | None = None,
) -> Page | None:
    browser_page: BrowserPage = await context.new_page()
    browser_page.set_default_navigation_timeout(PLAYWRIGHT_TIMEOUT_MS)
    try:
        record_opened(stats)
        try:
            response = await browser_page.goto(url, wait_until="commit", timeout=PLAYWRIGHT_TIMEOUT_MS)
            if stats is not None:
                stats.goto_ok += 1
        except PlaywrightTimeoutError:
            if stats is not None:
                stats.goto_timeout += 1
            response = None
        except Exception:
            if stats is not None:
                stats.goto_error += 1
            return None
        if response is not None and response.status >= 400:
            return None
        try:
            await browser_page.wait_for_load_state("domcontentloaded", timeout=PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        await browser_page.wait_for_timeout(PLAYWRIGHT_POST_LOAD_WAIT_MS)
        final_url = normalize_url(browser_page.url, allow_ignored_extensions=True)
        if not final_url or not is_internal_url(final_url, allowed_host):
            return None
        html = await read_rendered_html(browser_page, stats=stats)
        if not html:
            return None
        page = parse_page(html, final_url, allowed_host, depth=depth, path=path[:-1] + [final_url])
        if len(page.links) < 3:
            retry_page = await parse_after_link_wait(browser_page, final_url, allowed_host, depth=depth, path=path, stats=stats)
            if retry_page is not None and (has_text_feature(retry_page) or len(retry_page.links) > len(page.links)):
                page = retry_page
        if has_text_feature(page):
            if stats is not None:
                stats.extracted_any_feature += 1
                stats.content_extracted += 1
                stats.usable += 1
            return page
        return None
    except Exception:
        return None
    finally:
        await browser_page.close()

async def read_rendered_html(browser_page: BrowserPage, stats: CrawlStats | None = None) -> str:
    html = await safe_page_content(browser_page, stats=stats)
    if len(html) >= RENDERED_HTML_MIN_LENGTH:
        record_html_read(stats, html)
        return html

    if PLAYWRIGHT_POST_LOAD_WAIT_MS > 0:
        await browser_page.wait_for_timeout(PLAYWRIGHT_POST_LOAD_WAIT_MS * 2)
    try:
        await browser_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await browser_page.wait_for_timeout(100)
    except Exception:
        pass
    retry_html = await safe_page_content(browser_page, stats=stats)
    best_html = retry_html if len(retry_html) > len(html) else html
    record_html_read(stats, best_html)
    return best_html

async def safe_page_content(browser_page: BrowserPage, *, attempts: int = 3, stats: CrawlStats | None = None) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            html = (await browser_page.content()).strip()
            if html:
                return html
        except Exception as exc:
            last_error = exc
        try:
            html = await browser_page.evaluate(
                "() => document.documentElement ? document.documentElement.outerHTML : ''"
            )
            if isinstance(html, str) and html.strip():
                return html.strip()
        except Exception as exc:
            last_error = exc
        try:
            await browser_page.wait_for_selector("body", timeout=500)
        except Exception:
            pass
        try:
            html = await browser_page.locator("html").evaluate("el => el.outerHTML", timeout=500)
            if isinstance(html, str) and html.strip():
                return html.strip()
        except Exception as exc:
            last_error = exc
        try:
            await browser_page.wait_for_load_state("domcontentloaded", timeout=PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS)
        except Exception:
            pass
        try:
            await browser_page.wait_for_timeout(150 * (attempt + 1))
        except Exception:
            break
    if last_error is not None and stats is not None:
        stats.content_read_error += 1
    return ""

def record_html_read(stats: CrawlStats | None, html: str) -> None:
    if stats is None or not html:
        return
    stats.content_read_ok += 1
    stats.html_non_empty += 1
    stats.rendered += 1
    if len(html) >= RENDERED_HTML_MIN_LENGTH:
        stats.html_length_gt_threshold += 1
    else:
        stats.html_too_short += 1

async def parse_after_link_wait(
    browser_page: BrowserPage,
    final_url: str,
    allowed_host: str,
    *,
    depth: int | None,
    path: list[str],
    stats: CrawlStats | None = None,
) -> Page | None:
    try:
        await browser_page.wait_for_selector("a[href]", timeout=2000)
    except PlaywrightTimeoutError:
        pass
    if PLAYWRIGHT_POST_LOAD_WAIT_MS > 0:
        await browser_page.wait_for_timeout(PLAYWRIGHT_POST_LOAD_WAIT_MS * 2)
    html = await safe_page_content(browser_page, stats=stats)
    if not html:
        return None
    return parse_page(html, final_url, allowed_host, depth=depth, path=path[:-1] + [final_url])

def record_opened(stats: CrawlStats | None) -> None:
    if stats is None:
        return
    stats.opened += 1
    stats.opened_in_browser += 1

