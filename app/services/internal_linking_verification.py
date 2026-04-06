from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from app.services.frontier import CrawlNode, prioritize, score_link
from app.services.fetcher import FetchSession
from app.services.internal_linking_models import MAX_RECOMMENDATION_SOURCE_DEPTH, TargetVerificationResult
from app.services.link_placement import CrawledPageSnapshot
from app.services.parser import is_internal_url, normalize_url
from app.settings import get_settings


settings = get_settings()


class InternalLinkingVerificationMixin:
    @staticmethod
    def _candidate_branch_bonus(url: str, candidate_urls: set[str]) -> int:
        url_parts = [part for part in urlsplit(url).path.split("/") if part]
        if not url_parts:
            return 0

        best_bonus = 0
        for candidate_url in candidate_urls:
            candidate_parts = [part for part in urlsplit(candidate_url).path.split("/") if part]
            shared = 0
            for url_part, candidate_part in zip(url_parts, candidate_parts):
                if url_part != candidate_part:
                    break
                shared += 1

            bonus = shared * 20
            if shared == len(url_parts) and len(candidate_parts) >= len(url_parts):
                bonus += 25
            best_bonus = max(best_bonus, bonus)
        return best_bonus

    async def _verify_candidate_depths(
        self,
        *,
        client: FetchSession,
        candidate_urls: list[str],
        crawled_pages: dict[str, CrawledPageSnapshot],
    ) -> dict[str, int]:
        max_source_depth = min(settings.good_depth_threshold - 1, MAX_RECOMMENDATION_SOURCE_DEPTH)
        if max_source_depth < 0 or not self._start_url:
            return {}

        verified: dict[str, int] = {}
        remaining: set[str] = set()
        for url in candidate_urls:
            normalized = normalize_url(url)
            if not normalized:
                continue
            if self._target.url and self._target.url_matches(normalized):
                continue
            existing = crawled_pages.get(normalized)
            if existing is not None and existing.depth is not None and existing.depth <= max_source_depth:
                verified[normalized] = existing.depth
                continue
            remaining.add(normalized)

        if not remaining:
            return verified

        current_level: list[CrawlNode] = [CrawlNode(url=self._start_url, depth=0, path=[self._start_url])]
        visited: set[str] = {self._start_url}
        while current_level and remaining:
            if self._budget_exhausted():
                break
            level_candidates: dict[str, CrawlNode] = {}
            tasks = [asyncio.create_task(self._fetch_node(client, node)) for node in self._limit_nodes(current_level)]
            try:
                for task in asyncio.as_completed(tasks):
                    node, page = await task
                    if self._budget_exhausted():
                        self._cancel_pending(tasks)
                        break
                    if page is None:
                        continue
                    snapshot = self._placement_recommender.build_snapshot(
                        url=page.url,
                        title=page.title,
                        h1=page.h1,
                        depth=node.depth,
                        text=page.text,
                        is_indexable=page.is_indexable,
                        links_to_target=bool(self._target.url and any(self._target.url_matches(link.url) for link in page.links)),
                    )
                    self._remember_crawled_page(crawled_pages, snapshot)
                    if node.url in remaining:
                        verified[node.url] = node.depth
                        remaining.remove(node.url)
                        if not remaining:
                            self._cancel_pending(tasks)
                            break
                    if node.depth >= max_source_depth:
                        continue
                    for link in page.links:
                        if not is_internal_url(link.url, self._allowed_host):
                            continue
                        if self._target.url and self._target.url_matches(link.url):
                            continue
                        next_depth = node.depth + 1
                        if link.url in remaining and next_depth <= max_source_depth:
                            verified[link.url] = next_depth
                            remaining.remove(link.url)
                            if not remaining:
                                self._cancel_pending(tasks)
                                break
                        if next_depth > max_source_depth or link.url in visited:
                            continue
                        visited.add(link.url)
                        candidate = CrawlNode(
                            url=link.url,
                            depth=next_depth,
                            path=node.path + [link.url],
                            score=(
                                score_link(link.url, link.anchor_text, self._target.priority_terms)
                                + self._candidate_branch_bonus(link.url, remaining)
                            ),
                        )
                        existing_candidate = level_candidates.get(link.url)
                        if existing_candidate is None or candidate.score > existing_candidate.score:
                            level_candidates[link.url] = candidate
                    if not remaining:
                        break
                if not remaining:
                    break
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
            current_level = self._limit_nodes(prioritize(list(level_candidates.values())))
        return verified

    async def _verify_target_path(
        self,
        *,
        client: FetchSession,
        crawled_pages: dict[str, CrawledPageSnapshot],
        discovered_urls: set[str],
        max_depth: int,
        reserve_seconds: float = 0.0,
    ) -> TargetVerificationResult:
        if max_depth < 0 or not self._start_url or not self._target.url:
            return TargetVerificationResult()
        if self._target.url_matches(self._start_url):
            return TargetVerificationResult(steps_to_target=0, path=[self._start_url])

        current_level: list[CrawlNode] = [CrawlNode(url=self._start_url, depth=0, path=[self._start_url])]
        visited: set[str] = {self._start_url}
        fetched_count = 0
        batch_size = max(settings.max_crawl_level_size, 1)
        while current_level:
            if self._budget_exhausted(reserve_seconds=reserve_seconds):
                break
            level_candidates: dict[str, CrawlNode] = {}
            for offset in range(0, len(current_level), batch_size):
                if self._budget_exhausted(reserve_seconds=reserve_seconds):
                    return TargetVerificationResult(pages_fetched=fetched_count)
                tasks = [asyncio.create_task(self._fetch_node(client, node)) for node in current_level[offset : offset + batch_size]]
                try:
                    for task in asyncio.as_completed(tasks):
                        node, page = await task
                        if self._budget_exhausted(reserve_seconds=reserve_seconds):
                            self._cancel_pending(tasks)
                            return TargetVerificationResult(pages_fetched=fetched_count)
                        if page is None:
                            continue
                        fetched_count += 1
                        snapshot = self._placement_recommender.build_snapshot(
                            url=page.url,
                            title=page.title,
                            h1=page.h1,
                            depth=node.depth,
                            text=page.text,
                            is_indexable=page.is_indexable,
                            links_to_target=bool(self._target.url and any(self._target.url_matches(link.url) for link in page.links)),
                        )
                        self._remember_crawled_page(crawled_pages, snapshot)
                        if self._target.url_matches(node.url):
                            return TargetVerificationResult(
                                steps_to_target=node.depth,
                                path=node.path,
                                pages_fetched=fetched_count,
                            )
                        if node.depth >= max_depth:
                            continue
                        next_depth = node.depth + 1
                        for link in page.links:
                            if not is_internal_url(link.url, self._allowed_host):
                                continue
                            discovered_urls.add(link.url)
                            if self._target.url_matches(link.url):
                                return TargetVerificationResult(
                                    steps_to_target=next_depth,
                                    path=node.path + [link.url],
                                    pages_fetched=fetched_count,
                                )
                            if next_depth > max_depth or link.url in visited:
                                continue
                            visited.add(link.url)
                            level_candidates[link.url] = CrawlNode(
                                url=link.url,
                                depth=next_depth,
                                path=node.path + [link.url],
                            )
                finally:
                    await asyncio.gather(*tasks, return_exceptions=True)
            current_level = list(level_candidates.values())
        return TargetVerificationResult(pages_fetched=fetched_count)
