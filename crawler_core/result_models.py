from __future__ import annotations

from models import CrawlStats, Page, SimplifiedModel


class CrawlResult(SimplifiedModel):
    home_url: str
    target_url: str
    target_page: Page | None
    target_status: int | None = None
    target_error: str = ""
    pages: dict[str, Page]
    discovered_urls: set[str]
    stats: CrawlStats
    found: bool
    steps_to_target: int | None
    path: list[str]
    robots_checked: bool
    robots_available: bool
    sitemap_checked: bool
    found_in_sitemap: bool

class TargetFetchResult(SimplifiedModel):
    page: Page | None = None
    status: int | None = None
    final_url: str = ""
    error: str = ""

    @property
    def is_unavailable(self) -> bool:
        return self.status is not None and self.status >= 400

