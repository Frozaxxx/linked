from __future__ import annotations

from pydantic import Field

from app.models import SeoLinkedModel


class ExtractedLink(SeoLinkedModel):
    url: str
    anchor_text: str


class ParsedPage(SeoLinkedModel):
    url: str
    title: str
    h1: str
    text: str
    links: list[ExtractedLink]
    is_indexable: bool
    canonical_url: str | None


class ParsedSitemap(SeoLinkedModel):
    page_urls: list[str] = Field(default_factory=list)
    nested_sitemaps: list[str] = Field(default_factory=list)


class ParsedRobotsTxt(SeoLinkedModel):
    allow_rules: tuple[str, ...] = ()
    disallow_rules: tuple[str, ...] = ()
    sitemap_urls: list[str] = Field(default_factory=list)

    def is_allowed(self, url: str) -> bool:
        from app.services.parser.robots import best_robots_match_length, robots_url_path

        path = robots_url_path(url)
        allow_match_length = best_robots_match_length(path, self.allow_rules)
        disallow_match_length = best_robots_match_length(path, self.disallow_rules)
        if disallow_match_length < 0:
            return True
        return allow_match_length >= disallow_match_length
