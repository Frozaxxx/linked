from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from urllib.parse import urlsplit

from app.models import CrawlNode, SitemapSnapshot
from app.services.fetcher import FetchSession
from app.services.internal_linking.constants import SITEMAP_WAIT_TIMEOUT_SECONDS
from app.services.matcher import extract_url_terms
from app.services.parser import (
    ParsedPage,
    get_site_root,
    is_internal_url,
    normalize_url,
    parse_html,
    parse_robots_txt,
    parse_sitemap,
)
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class InternalLinkingDiscoveryMixin:
    async def _collect_robots_snapshot(self, client: FetchSession) -> None:
        if not self._start_url:
            return

        robots_url = normalize_url("robots.txt", get_site_root(self._start_url), allow_ignored_extensions=True)
        if not robots_url:
            return

        self._robots_snapshot.checked = True
        async with self._semaphore:
            document = await self._fetcher.fetch(
                client,
                robots_url,
                render_html=False,
                total_timeout_seconds=self._remaining_fetch_budget_seconds(),
            )

        if document is None:
            logger.info("robots.txt is unavailable or could not be fetched: %s", robots_url)
            return

        self._robots_snapshot.available = True
        self._robots_policy = parse_robots_txt(
            document.body,
            get_site_root(self._start_url),
            self._allowed_host,
            settings.robots_user_agent,
        )
        self._robots_snapshot.sitemap_urls.update(self._robots_policy.sitemap_urls)
        logger.info(
            "robots.txt loaded: start_url=%s sitemap_urls=%s",
            self._start_url,
            len(self._robots_snapshot.sitemap_urls),
        )

    async def _resolve_target_metadata(self, client: FetchSession) -> int:
        if not self._requested_target_url or not is_internal_url(self._requested_target_url, self._allowed_host):
            return 0
        if not self._is_allowed_by_robots(self._requested_target_url):
            logger.warning("Requested target URL is blocked by robots.txt: %s", self._requested_target_url)
            return 0
        document = await self._fetcher.fetch(
            client,
            self._requested_target_url,
            total_timeout_seconds=self._remaining_fetch_budget_seconds(),
        )
        if document is None:
            logger.warning("Failed to fetch requested target URL metadata: %s", self._requested_target_url)
            return 0
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
            logger.warning(
                "Requested target URL resolved outside of the allowed host: requested=%s final=%s",
                self._requested_target_url,
                document.final_url,
            )
            return 0
        page = parse_html(document.body, normalized_final_url, self._allowed_host)
        equivalent_urls = {self._requested_target_url, normalized_final_url}
        resolved_target_url = normalized_final_url
        if page.canonical_url and is_internal_url(page.canonical_url, self._allowed_host):
            equivalent_urls.add(page.canonical_url)
            resolved_target_url = page.canonical_url
        resolved_title = self._requested_target_title or page.h1 or page.title or None
        self._replace_target(
            primary_url=resolved_target_url,
            title=resolved_title,
            equivalent_urls=equivalent_urls,
        )
        return 1

    async def _await_sitemap_for_recommendations(
        self,
        sitemap_task: asyncio.Task,
        sitemap: SitemapSnapshot,
        *,
        pages_fetched: int,
        reserve_seconds: float = 0.0,
    ) -> None:
        if sitemap.completed or sitemap_task.done():
            return
        timeout = SITEMAP_WAIT_TIMEOUT_SECONDS
        if pages_fetched == 0:
            timeout = max(timeout, settings.request_timeout_seconds)
        remaining_budget = self._remaining_budget_seconds()
        if remaining_budget is not None:
            usable_budget = remaining_budget - reserve_seconds
            if usable_budget <= 0:
                logger.info("Skipping sitemap wait because no budget remains for recommendations.")
                return
            timeout = min(timeout, usable_budget)
        try:
            await asyncio.wait_for(asyncio.shield(sitemap_task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.info("Timed out while waiting for sitemap recommendations: timeout=%.3fs", timeout)
            return

    async def _collect_sitemap_snapshot(self, client: FetchSession, sitemap: SitemapSnapshot) -> None:
        sitemap_queue = list(self._robots_snapshot.sitemap_urls)
        fallback_sitemap = normalize_url("sitemap.xml", get_site_root(self._start_url), allow_ignored_extensions=True)
        if fallback_sitemap and fallback_sitemap not in sitemap_queue:
            sitemap_queue.append(fallback_sitemap)
        self._prioritize_sitemap_queue(sitemap_queue, checked=set())
        checked: set[str] = set()
        try:
            while sitemap_queue:
                if self._budget_exhausted() or self._sitemap_budget_exhausted(sitemap):
                    logger.info("Stopping sitemap collection because sitemap budget is exhausted.")
                    return
                if len(checked) >= settings.sitemap_max_files:
                    logger.info(
                        "Stopping sitemap collection because sitemap file limit is reached: limit=%s",
                        settings.sitemap_max_files,
                    )
                    return
                if len(sitemap.page_urls) >= settings.sitemap_max_page_urls:
                    logger.info(
                        "Stopping sitemap collection because sitemap URL limit is reached: limit=%s",
                        settings.sitemap_max_page_urls,
                    )
                    return
                sitemap_url = sitemap_queue.pop(0)
                if not sitemap_url or sitemap_url in checked:
                    continue
                checked.add(sitemap_url)
                sitemap.checked = True
                document = await self._fetcher.fetch(
                    client,
                    sitemap_url,
                    render_html=False,
                    total_timeout_seconds=self._remaining_sitemap_fetch_budget_seconds(sitemap),
                )
                if document is None:
                    logger.info("Sitemap fetch returned no document: %s", sitemap_url)
                    continue
                sitemap_payload = document.body_bytes if document.body_bytes is not None else document.body
                parsed = parse_sitemap(sitemap_payload, self._allowed_host)
                allowed_page_urls = {url for url in parsed.page_urls if self._is_allowed_by_robots(url)}
                sitemap.page_urls.update(allowed_page_urls)
                if self._target.url and any(self._target.url_matches(url) for url in allowed_page_urls):
                    sitemap.found_target = True
                for nested_sitemap in parsed.nested_sitemaps:
                    if nested_sitemap not in checked and nested_sitemap not in sitemap_queue:
                        sitemap_queue.append(nested_sitemap)
                self._prioritize_sitemap_queue(sitemap_queue, checked=checked)
            sitemap.completed = True
        finally:
            sitemap.finished_at = perf_counter()
            logger.info(
                "Sitemap collection finished: checked=%s page_urls=%s nested_remaining=%s found_target=%s completed=%s",
                len(checked),
                len(sitemap.page_urls),
                len(sitemap_queue),
                sitemap.found_target,
                sitemap.completed,
            )

    def _remaining_sitemap_fetch_budget_seconds(self, sitemap: SitemapSnapshot) -> float:
        remaining_sitemap = self._remaining_sitemap_budget_seconds(sitemap)
        remaining_total = self._remaining_fetch_budget_seconds()
        if remaining_total is None:
            return remaining_sitemap
        return min(remaining_sitemap, remaining_total)

    def _remaining_sitemap_budget_seconds(self, sitemap: SitemapSnapshot) -> float:
        if sitemap.started_at is None:
            return max(settings.sitemap_time_budget_seconds, 0.0)
        elapsed = perf_counter() - sitemap.started_at
        return max(settings.sitemap_time_budget_seconds - elapsed, 0.0)

    def _sitemap_budget_exhausted(self, sitemap: SitemapSnapshot) -> bool:
        return self._remaining_sitemap_budget_seconds(sitemap) <= 0

    def _prioritize_sitemap_queue(self, sitemap_queue: list[str], *, checked: set[str]) -> None:
        sitemap_queue.sort(key=lambda url: self._sitemap_queue_sort_key(url, checked=checked))

    def _sitemap_queue_sort_key(self, url: str, *, checked: set[str]) -> tuple[int, str]:
        if url in checked:
            return (10_000, url)
        return (-self._score_sitemap_url(url), url)

    def _score_sitemap_url(self, url: str) -> int:
        score = self._candidate_branch_bonus(url, {self._target.url}) if self._target.url else 0
        sitemap_terms = set(extract_url_terms(url))
        if sitemap_terms:
            target_terms = (
                set(self._target.priority_terms)
                | set(self._target.signature_terms)
                | set(self._target.branch_terms)
                | set(self._target.core_branch_terms)
            )
            score += len(sitemap_terms & target_terms) * 12
        return score

    async def _fetch_node(self, client: FetchSession, node: CrawlNode) -> tuple[CrawlNode, ParsedPage | None]:
        if not self._is_allowed_by_robots(node.url):
            return node, None
        if self._is_html_403_branch_blocked(node.url):
            logger.debug("Skipping HTML URL from 403-blocked branch: %s", node.url)
            return node, None
        async with self._semaphore:
            document = await self._fetcher.fetch(
                client,
                node.url,
                total_timeout_seconds=self._remaining_fetch_budget_seconds(),
                failure_status_callback=self._record_html_fetch_failure_status,
            )
        if document is None:
            return node, None
        normalized_final_url = normalize_url(document.final_url)
        if not normalized_final_url or not is_internal_url(normalized_final_url, self._allowed_host):
            return node, None
        if not self._is_allowed_by_robots(normalized_final_url):
            return node, None
        page = parse_html(document.body, normalized_final_url, self._allowed_host)
        if normalized_final_url != node.url:
            node = CrawlNode(
                url=normalized_final_url,
                depth=node.depth,
                path=node.path[:-1] + [normalized_final_url],
                score=node.score,
                sitemap_boosted=node.sitemap_boosted,
            )
        return node, page

    def _record_html_fetch_failure_status(self, status_code: int, url: str) -> None:
        if status_code != 403:
            return
        branch_key = self._html_403_branch_key(url)
        if not branch_key:
            return
        count = self._html_403_branch_counts.get(branch_key, 0) + 1
        self._html_403_branch_counts[branch_key] = count
        if count >= settings.html_403_branch_skip_threshold:
            self._html_403_blocked_branches.add(branch_key)
            logger.info("HTML branch blocked after repeated 403 responses: branch=%s count=%s", branch_key, count)

    def _is_html_403_branch_blocked(self, url: str) -> bool:
        branch_key = self._html_403_branch_key(url)
        return bool(branch_key and branch_key in self._html_403_blocked_branches)

    def _html_403_branch_key(self, url: str) -> str | None:
        parsed = urlsplit(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts:
            return None
        target_parts = [part for part in urlsplit(self._target.url or "").path.split("/") if part]
        if target_parts and path_parts[0] == target_parts[0]:
            branch_parts = path_parts[: min(len(path_parts), max(3, len(target_parts) - 1))]
        else:
            branch_parts = path_parts[:1]
        if not branch_parts:
            return None
        return "/" + "/".join(branch_parts)
