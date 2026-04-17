from __future__ import annotations

from app.models.base import SeoLinkedModel


class SearchTarget(SeoLinkedModel):
    url: str | None
    title: str | None
    text: str | None
    canonical_url: str | None = None
    equivalent_urls: tuple[str, ...] = ()

    @property
    def thematic_terms(self) -> tuple[str, ...]:
        from app.services.matcher import extract_terms, extract_url_terms

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
        from app.services.matcher import extract_terms, extract_weighted_url_terms

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
        from app.services.matcher import extract_terms, extract_url_signature_terms

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
        from app.services.matcher import extract_url_branch_terms

        terms: list[str] = []

        for term in extract_url_branch_terms(self.url):
            if term not in terms:
                terms.append(term)

        return tuple(terms)

    @property
    def core_branch_terms(self) -> tuple[str, ...]:
        from app.services.matcher import extract_url_core_branch_terms

        terms: list[str] = []

        for term in extract_url_core_branch_terms(self.url):
            if term not in terms:
                terms.append(term)

        return tuple(terms)

    def url_matches(self, candidate_url: str) -> bool:
        return self.url_match_reason(candidate_url) is not None

    def url_match_reason(self, candidate_url: str) -> str | None:
        if not candidate_url:
            return None

        if self.url and candidate_url == self.url:
            return "url"

        if self.canonical_url and candidate_url == self.canonical_url:
            return "canonical_url"

        if candidate_url in self.equivalent_urls:
            return "equivalent_url"

        return None

    def page_matches(self, candidate_url: str, title: str, text: str) -> list[str]:
        from app.services.matcher import normalize_text, terms_overlap_match

        matched_by: list[str] = []

        url_match_reason = self.url_match_reason(candidate_url)
        if url_match_reason:
            matched_by.append(url_match_reason)

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
