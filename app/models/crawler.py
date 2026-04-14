from __future__ import annotations

from app.models.base import SeoLinkedModel


class CrawlNode(SeoLinkedModel):
    url: str
    depth: int
    path: list[str]
    score: int = 0
    sitemap_boosted: bool = False
