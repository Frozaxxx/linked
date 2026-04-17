from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

from app.models import SearchTarget
from app.services.stemming import stem_token


TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
SPACE_RE = re.compile(r"\s+")
GENERIC_URL_TERMS = {
    "about",
    "archive",
    "archives",
    "article",
    "articles",
    "category",
    "categories",
    "default",
    "feed",
    "home",
    "index",
    "page",
    "pages",
    "post",
    "posts",
    "search",
    "section",
    "sections",
    "tag",
    "tags",
    "topic",
    "topics",
    "use",
    "using",
}
SIGNATURE_STOP_TERMS = {
    "about",
    "area",
    "areas",
    "completed",
    "component",
    "components",
    "doc",
    "docs",
    "focus",
    "foundation",
    "foundations",
    "guide",
    "guides",
    "overview",
    "project",
    "projects",
}
CONTENT_STOP_TERMS = {
    stem_token(token)
    for token in {
        "article",
        "articles",
        "award",
        "awards",
        "collaboration",
        "complete",
        "completed",
        "contest",
        "contests",
        "event",
        "events",
        "feature",
        "features",
        "gallery",
        "galleries",
        "image",
        "images",
        "network",
        "news",
        "photo",
        "photos",
        "program",
        "programs",
        "project",
        "projects",
        "release",
        "releases",
        "region",
        "regions",
        "regional",
        "report",
        "reports",
        "resource",
        "resources",
        "update",
        "updates",
        "winner",
        "winners",
    }
}


def normalize_text(value: str | None) -> str:
    return SPACE_RE.sub(" ", (value or "").replace("ё", "е").replace("Ё", "Е")).strip().casefold()


def extract_terms(value: str | None) -> list[str]:
    normalized = normalize_text(value)
    terms: list[str] = []
    for token in TOKEN_RE.findall(normalized):
        if len(token) < 3 or token.isdigit():
            continue
        stemmed = stem_token(token)
        if len(stemmed) < 3 or stemmed.isdigit() or stemmed in CONTENT_STOP_TERMS or stemmed in terms:
            continue
        terms.append(stemmed)
    return terms


def extract_url_terms(url: str | None) -> list[str]:
    if not url:
        return []

    parsed = urlsplit(url)
    terms: list[str] = []

    path_parts = [part for part in parsed.path.split("/") if part]
    for part in reversed(path_parts):
        for term in extract_terms(part):
            if term in GENERIC_URL_TERMS or term in terms:
                continue
            terms.append(term)

    return terms


def extract_weighted_url_terms(url: str | None) -> dict[str, int]:
    if not url:
        return {}

    parsed = urlsplit(url)
    weighted_terms: dict[str, int] = {}
    path_parts = [part for part in parsed.path.split("/") if part]

    for depth, part in enumerate(reversed(path_parts)):
        segment_weight = max(2, 12 - depth * 3)
        for term in extract_terms(part):
            if term in GENERIC_URL_TERMS or term in weighted_terms:
                continue
            weighted_terms[term] = segment_weight

    return weighted_terms


def extract_url_signature_terms(url: str | None) -> list[str]:
    if not url:
        return []

    parsed = urlsplit(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    signature_terms: list[str] = []

    for part in reversed(path_parts):
        for term in extract_terms(part):
            if term in GENERIC_URL_TERMS or term in SIGNATURE_STOP_TERMS or term in signature_terms:
                continue
            signature_terms.append(term)
        if signature_terms:
            break

    if signature_terms:
        return signature_terms

    for term in extract_url_terms(url):
        if term not in signature_terms:
            signature_terms.append(term)
        if len(signature_terms) >= 2:
            break

    return signature_terms


def extract_url_branch_terms(url: str | None) -> list[str]:
    if not url:
        return []

    parsed = urlsplit(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) <= 1:
        return []

    branch_terms: list[str] = []
    for part in reversed(path_parts[:-1]):
        for term in extract_terms(part):
            if term in GENERIC_URL_TERMS or term in SIGNATURE_STOP_TERMS or term in branch_terms:
                continue
            branch_terms.append(term)
        if len(branch_terms) >= 5:
            break

    return branch_terms


def extract_url_core_branch_terms(url: str | None) -> list[str]:
    if not url:
        return []

    parsed = urlsplit(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) <= 1:
        return []

    for part in reversed(path_parts[:-1]):
        core_terms: list[str] = []
        for term in extract_terms(part):
            if term in GENERIC_URL_TERMS or term in SIGNATURE_STOP_TERMS or term in core_terms:
                continue
            core_terms.append(term)
        if core_terms:
            return core_terms[:3]

    return []


def terms_overlap_match(value: str | None, candidate: str | None, *, ratio: float) -> bool:
    target_terms = extract_terms(value)
    if not target_terms:
        return False

    candidate_terms = set(extract_terms(candidate))
    if not candidate_terms:
        return False

    overlap = sum(1 for term in target_terms if term in candidate_terms)
    if overlap == 0:
        return False
    if len(target_terms) == 1:
        return overlap == 1

    required = max(2, math.ceil(len(target_terms) * ratio))
    return overlap >= required
