from __future__ import annotations

import re

from app.models import CrawledPageSnapshot, PlacementRecommendation, RankedRecommendation


MAX_BODY_TEXT = 6000
MAX_RECOMMENDATIONS = 3
MAX_TERMS_PER_FIELD = 48
MIN_RECOMMENDATION_SOURCE_DEPTH = 1
MAX_RECOMMENDATION_SOURCE_DEPTH = 3

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
    "comment",
    "comment-modal",
    "comments",
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
    "modal",
    "new-release",
    "news",
    "news-release",
    "news-releases",
    "newsroom",
    "photo",
    "photos",
    "press",
    "press-release",
    "pressroom",
    "privacy",
    "release",
    "releases",
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
    "webmaster",
    "wp-admin",
    "wp-login",
}

TECHNICAL_QUERY_TOKENS = {
    "filter",
    "filters",
    "email",
    "page",
    "paged",
    "q",
    "query",
    "replytocom",
    "s",
    "search",
    "sort",
    "url",
    "webmaster",
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
