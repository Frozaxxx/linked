from __future__ import annotations

from dataclasses import dataclass


SITEMAP_SCORE_BONUS = 6


@dataclass(slots=True)
class CrawlNode:
    url: str
    depth: int
    path: list[str]
    score: int = 0
    sitemap_boosted: bool = False


def score_link(url: str, anchor_text: str, priority_terms: tuple[str, ...]) -> int:
    score = 0
    normalized_url = url.casefold()
    normalized_anchor = anchor_text.casefold()

    for term in priority_terms:
        if term in normalized_url:
            score += 3
        if term in normalized_anchor:
            score += 2

    score -= normalized_url.count("/") // 3
    return score


def apply_sitemap_bonus(nodes: list[CrawlNode], sitemap_urls: set[str]) -> None:
    if not sitemap_urls:
        return

    for node in nodes:
        if not node.sitemap_boosted and node.url in sitemap_urls:
            node.score += SITEMAP_SCORE_BONUS
            node.sitemap_boosted = True


def prioritize(nodes: list[CrawlNode]) -> list[CrawlNode]:
    return sorted(nodes, key=lambda node: (-node.score, node.url))
