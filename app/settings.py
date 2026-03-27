from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SEO Internal Linking Analyzer"
    app_version: str = "0.1.0"
    app_description: str = (
        "API for internal linking analysis. The service starts BFS immediately and uses sitemap data "
        "as a live prioritization signal while the crawl is running."
    )
    analyze_summary: str = "Analyze internal linking to a target page"

    request_timeout_seconds: float = 10.0
    request_retry_count: int = 2
    crawl_concurrency: int = 8
    good_depth_threshold: int = 4

    fetch_user_agent: str = "seo-linked/0.1"
    fetch_accept_header: str = "text/html,application/xhtml+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
