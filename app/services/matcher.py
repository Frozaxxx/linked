from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

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
HOST_STOP_TOKENS = {
    "com",
    "edu",
    "gov",
    "net",
    "org",
    "ru",
    "www",
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


@dataclass(slots=True)
class SearchTarget:
    url: str | None
    title: str | None
    text: str | None
    equivalent_urls: tuple[str, ...] = ()

    @property
    def thematic_terms(self) -> tuple[str, ...]:
        terms: list[str] = []
        for source in (self.title or "", self.text or ""):
            for token in extract_terms(source):
                if token not in terms:
                    terms.append(token)

        for token in extract_url_terms(self.url):
            if token not in terms:
                terms.append(token)

        return tuple(terms)

    @property
    def priority_terms(self) -> tuple[str, ...]:
        return self.thematic_terms[:12]

    @property
    def term_weights(self) -> dict[str, int]:
        weights: dict[str, int] = {}

        for index, term in enumerate(extract_terms(self.title)):
            weights.setdefault(term, max(5, 10 - index))

        for index, term in enumerate(extract_terms(self.text)):
            weights.setdefault(term, max(4, 8 - index))

        for term, weight in extract_weighted_url_terms(self.url).items():
            existing = weights.get(term, 0)
            if weight > existing:
                weights[term] = weight

        return weights

    @property
    def signature_terms(self) -> tuple[str, ...]:
        terms: list[str] = []

        for term in extract_url_signature_terms(self.url):
            if term not in terms:
                terms.append(term)

        if terms:
            return tuple(terms)

        for source in (self.title or "", self.text or ""):
            for term in extract_terms(source):
                if term not in terms:
                    terms.append(term)
                if len(terms) >= 2:
                    break
            if terms:
                break

        return tuple(terms)

    @property
    def branch_terms(self) -> tuple[str, ...]:
        terms: list[str] = []

        for term in extract_url_branch_terms(self.url):
            if term not in terms:
                terms.append(term)

        return tuple(terms)

    @property
    def core_branch_terms(self) -> tuple[str, ...]:
        terms: list[str] = []

        for term in extract_url_core_branch_terms(self.url):
            if term not in terms:
                terms.append(term)

        return tuple(terms)

    def url_matches(self, candidate_url: str) -> bool:
        if not candidate_url:
            return False

        if self.url and candidate_url == self.url:
            return True

        return candidate_url in self.equivalent_urls

    def page_matches(self, candidate_url: str, title: str, text: str) -> list[str]:
        matched_by: list[str] = []

        if self.url_matches(candidate_url):
            matched_by.append("url")

        page_title = normalize_text(title)
        target_title = normalize_text(self.title)
        if target_title and (
            target_title in page_title or terms_overlap_match(self.title, title, ratio=0.75)
        ):
            matched_by.append("title")

        page_text = normalize_text(text)
        target_text = normalize_text(self.text)
        if target_text and (
            target_text in page_text or terms_overlap_match(self.text, text, ratio=0.85)
        ):
            matched_by.append("content")

        return matched_by
