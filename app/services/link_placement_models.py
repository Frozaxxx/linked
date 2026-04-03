from __future__ import annotations

import re
from dataclasses import dataclass


MAX_BODY_TEXT = 6000
MAX_RECOMMENDATIONS = 3
MAX_TERMS_PER_FIELD = 48
MIN_RECOMMENDATION_SOURCE_DEPTH = 1
MAX_RECOMMENDATION_SOURCE_DEPTH = 3
MIN_RECOMMENDATION_CONTEXT_SCORE = 8
MIN_PATH_CONTEXT_SCORE = 8
MIN_BRANCH_CONTEXT_SCORE = 8
MIN_CORE_BRANCH_SCORE = 5
MIN_STRONG_TOPIC_SCORE = 18
MIN_STRONG_SIGNATURE_COUNT = 3

TECHNICAL_URL_TOKENS = {
    "404",
    "account",
    "admin",
    "author",
    "basket",
    "cart",
    "checkout",
    "compare",
    "comparison",
    "cookie",
    "cookies",
    "error",
    "feed",
    "filter",
    "filters",
    "gallery",
    "galleries",
    "image",
    "images",
    "login",
    "logout",
    "media",
    "multimedia",
    "news",
    "newsroom",
    "photo",
    "photos",
    "press",
    "press-release",
    "pressroom",
    "privacy",
    "search",
    "signin",
    "signup",
    "tag",
    "tags",
    "terms",
    "user",
    "video",
    "videos",
    "webinar",
    "webinars",
    "wp-admin",
    "wp-login",
}

TECHNICAL_QUERY_TOKENS = {
    "filter",
    "filters",
    "page",
    "paged",
    "q",
    "query",
    "replytocom",
    "s",
    "search",
    "sort",
}

TECHNICAL_TITLE_PHRASES = (
    "image gallery",
    "media advisory",
    "multimedia",
    "news release",
    "photo gallery",
    "privacy policy",
    "press release",
    "press room",
    "search results",
    "shopping cart",
    "sign in",
    "sign up",
    "terms of service",
    "terms of use",
    "video gallery",
)

RAW_TOKEN_RE = re.compile(r"[0-9a-z-]+")
TEXT_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")

ANCHOR_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "using",
    "with",
}

ANCHOR_LABEL_STOP_WORDS = {
    "region",
    "regions",
    "regional",
}


@dataclass(slots=True)
class PlacementRecommendation:
    source_url: str
    source_title: str | None
    source_depth: int | None
    projected_steps_to_target: int | None
    reason: str
    placement_hint: str
    anchor_hint: str | None
    confidence: str = "strong"


@dataclass(slots=True)
class CrawledPageSnapshot:
    url: str
    title: str
    depth: int | None
    normalized_title: str
    normalized_h1: str
    normalized_text: str
    url_terms: frozenset[str]
    title_terms: frozenset[str]
    h1_terms: frozenset[str]
    body_terms: frozenset[str]
    is_indexable: bool
    links_to_target: bool


@dataclass(slots=True)
class RankedRecommendation:
    recommendation: PlacementRecommendation
    score: int
