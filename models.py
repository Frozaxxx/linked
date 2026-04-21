from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SimplifiedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Link(SimplifiedModel):
    url: str
    anchor: str = ""


class Page(SimplifiedModel):
    url: str
    title: str = ""
    h1: str = ""
    text: str = ""
    breadcrumbs: list[str] = Field(default_factory=list)
    parent_section: str = ""
    internal_link_count: int = 0
    links: list[Link] = Field(default_factory=list)
    depth: int | None = None
    path: list[str] = Field(default_factory=list)


class Candidate(SimplifiedModel):
    url: str
    title: str = ""
    h1: str = ""
    parent_section: str = ""
    depth: int | None = None
    internal_link_count: int = 0
    score: float = 0.0
    reason: str = ""
    source: str = "parsed"
    confidence: str = "normal"


class CrawlStats(SimplifiedModel):
    discovered: int = 0
    opened: int = 0
    opened_in_browser: int = 0
    goto_ok: int = 0
    goto_timeout: int = 0
    goto_error: int = 0
    content_read_ok: int = 0
    content_read_error: int = 0
    html_length_gt_threshold: int = 0
    html_too_short: int = 0
    html_non_empty: int = 0
    extracted_any_feature: int = 0
    rendered: int = 0
    content_extracted: int = 0
    usable: int = 0


class AnalysisResult(SimplifiedModel):
    target_url: str
    target_status: int | None = None
    target_error: str = ""
    home_url: str
    found: bool
    steps_to_target: int | None
    path: list[str]
    linking_status: str
    poor_linking: bool
    pages_parsed: int
    pages_discovered: int
    crawl_stats: CrawlStats
    robots_checked: bool
    robots_available: bool
    sitemap_checked: bool
    found_in_sitemap: bool
    requested_top_k: int = 5
    local_candidates_count: int = 0
    returned_top_k: int = 0
    diagnostic_reasons: list[str] = Field(default_factory=list)
    local_top5: list[Candidate]
    llm_top3: list[Candidate]
    candidates: list[Candidate]
    llm_explanation: str
    rerank_source: str
    message: str

    def to_dict(self) -> dict:
        return self.model_dump()
