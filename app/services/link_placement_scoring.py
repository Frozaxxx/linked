from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from app.services.frontier import score_link
from app.services.link_placement_models import (
    RAW_TOKEN_RE,
    TECHNICAL_QUERY_TOKENS,
    TECHNICAL_TITLE_PHRASES,
    TECHNICAL_URL_TOKENS,
    CrawledPageSnapshot,
)
from app.services.matcher import extract_url_terms


class LinkPlacementScoringMixin:
    def score_source_url_soft(self, url: str) -> int:
        if self._is_target_url(url) or self._is_technical_url(url):
            return 0
        shared_path_bonus = self._shared_path_bonus(url)
        url_terms = set(extract_url_terms(url))
        overlap_score = self._weighted_overlap_score(url_terms)
        branch_score = self._weighted_overlap_score(self._branch_overlap_terms(url_terms))
        core_branch_score = self._weighted_overlap_score(self._core_branch_overlap_terms(url_terms))
        signature_score = self._weighted_overlap_score(self._signature_overlap_terms(url_terms))
        legacy_score = score_link(url, "", self._target.priority_terms)
        if not self._has_soft_semantic_signal(
            shared_path_bonus=shared_path_bonus,
            overlap_score=overlap_score,
            branch_score=branch_score,
            core_branch_score=core_branch_score,
            signature_score=signature_score,
        ):
            return 0
        return (
            shared_path_bonus * 2
            + overlap_score * 2
            + branch_score * 3
            + core_branch_score * 4
            + signature_score * 2
            + legacy_score
        )

    def _soft_candidate_score(self, snapshot: CrawledPageSnapshot) -> int:
        metadata_terms = snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms
        candidate_terms = metadata_terms | snapshot.body_terms
        overlap_score = self._weighted_overlap_score(candidate_terms)
        branch_score = self._weighted_overlap_score(self._branch_overlap_terms(metadata_terms))
        core_branch_score = self._weighted_overlap_score(self._core_branch_overlap_terms(metadata_terms))
        signature_score = self._weighted_overlap_score(self._signature_overlap_terms(candidate_terms))
        shared_path_bonus = self._shared_path_bonus(snapshot.url)
        legacy_score = score_link(snapshot.url, snapshot.title, self._target.priority_terms)
        phrase_bonus = 0
        if self._normalized_target_title:
            if self._normalized_target_title in snapshot.normalized_title:
                phrase_bonus += 16
            elif self._normalized_target_title in snapshot.normalized_h1:
                phrase_bonus += 12
            elif self._normalized_target_title in snapshot.normalized_text:
                phrase_bonus += 8
        if not self._has_soft_semantic_signal(
            shared_path_bonus=shared_path_bonus,
            overlap_score=overlap_score,
            branch_score=branch_score,
            core_branch_score=core_branch_score,
            signature_score=signature_score,
            phrase_bonus=phrase_bonus,
        ):
            return 0
        return (
            overlap_score * 2
            + branch_score * 3
            + core_branch_score * 4
            + signature_score * 2
            + shared_path_bonus
            + legacy_score
            + phrase_bonus
        )

    @staticmethod
    def _has_soft_semantic_signal(
        *,
        shared_path_bonus: int,
        overlap_score: int,
        branch_score: int,
        core_branch_score: int,
        signature_score: int,
        phrase_bonus: int = 0,
    ) -> bool:
        semantic_score = overlap_score + branch_score + core_branch_score + signature_score + phrase_bonus
        if semantic_score >= 6:
            return True
        if shared_path_bonus >= 15:
            return True
        return shared_path_bonus >= 10 and semantic_score >= 3

    def _weighted_overlap_score(self, candidate_terms: set[str] | frozenset[str]) -> int:
        return sum(self._target_term_weights.get(term, 0) for term in candidate_terms)

    def _overlapping_target_terms(self, candidate_terms: set[str] | frozenset[str]) -> set[str]:
        return {term for term in candidate_terms if term in self._target_term_weights}

    def _signature_overlap_terms(self, candidate_terms: set[str] | frozenset[str]) -> set[str]:
        return set(candidate_terms) & self._target_signature_terms

    def _branch_overlap_terms(self, candidate_terms: set[str] | frozenset[str]) -> set[str]:
        return set(candidate_terms) & self._target_branch_terms

    def _core_branch_overlap_terms(self, candidate_terms: set[str] | frozenset[str]) -> set[str]:
        return set(candidate_terms) & self._target_core_branch_terms

    @staticmethod
    def _is_technical_source(*, url: str, normalized_title: str, normalized_h1: str) -> bool:
        if LinkPlacementScoringMixin._is_technical_url(url):
            return True
        return any(phrase in normalized_title or phrase in normalized_h1 for phrase in TECHNICAL_TITLE_PHRASES)

    @staticmethod
    def _is_technical_url(url: str) -> bool:
        parsed = urlsplit(url)
        path_tokens = {
            token
            for part in parsed.path.casefold().split("/")
            for token in RAW_TOKEN_RE.findall(part)
            if token
        }
        if path_tokens & TECHNICAL_URL_TOKENS:
            return True
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            query_tokens = set(RAW_TOKEN_RE.findall(key.casefold()))
            query_tokens.update(RAW_TOKEN_RE.findall(value.casefold()))
            if query_tokens & TECHNICAL_QUERY_TOKENS:
                return True
        return False

    def _is_target_url(self, url: str) -> bool:
        return self._target.url_matches(url)

    def _shared_path_bonus(self, source_url: str) -> int:
        if not self._target.url:
            return 0
        source_parts = self._path_parts(source_url)
        target_parts = self._path_parts(self._target.url)
        shared = 0
        for source_part, target_part in zip(source_parts, target_parts):
            if source_part != target_part:
                break
            shared += 1
        return shared * 5
