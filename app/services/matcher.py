from __future__ import annotations

import re
from dataclasses import dataclass


TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-Я]+")
SPACE_RE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    return SPACE_RE.sub(" ", value or "").strip().casefold()


def extract_terms(value: str | None) -> list[str]:
    normalized = normalize_text(value)
    terms: list[str] = []
    for token in TOKEN_RE.findall(normalized):
        if len(token) >= 3 and token not in terms:
            terms.append(token)
    return terms


@dataclass(slots=True)
class SearchTarget:
    url: str | None
    title: str | None
    text: str | None

    @property
    def priority_terms(self) -> tuple[str, ...]:
        terms: list[str] = []
        for source in (self.url or "", self.title or "", self.text or ""):
            for token in extract_terms(source):
                if token not in terms:
                    terms.append(token)
                if len(terms) >= 12:
                    return tuple(terms)
        return tuple(terms)

    def url_matches(self, candidate_url: str) -> bool:
        return bool(self.url and candidate_url == self.url)

    def page_matches(self, candidate_url: str, title: str, text: str) -> list[str]:
        matched_by: list[str] = []

        if self.url_matches(candidate_url):
            matched_by.append("url")

        page_title = normalize_text(title)
        target_title = normalize_text(self.title)
        if target_title and target_title in page_title:
            matched_by.append("title")

        page_text = normalize_text(text)
        target_text = normalize_text(self.text)
        if target_text and target_text in page_text:
            matched_by.append("content")

        return matched_by
