from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from app.services.frontier import score_link
from app.services.link_placement_models import (
    MIN_BRANCH_CONTEXT_SCORE,
    MIN_CORE_BRANCH_SCORE,
    MIN_RECOMMENDATION_CONTEXT_SCORE,
    MIN_STRONG_SIGNATURE_COUNT,
    MIN_STRONG_TOPIC_SCORE,
    RAW_TOKEN_RE,
    TECHNICAL_QUERY_TOKENS,
    TECHNICAL_TITLE_PHRASES,
    TECHNICAL_URL_TOKENS,
    CrawledPageSnapshot,
)
from app.services.matcher import extract_url_terms


class LinkPlacementScoringMixin:
    def score_source_url(self, url: str) -> int:
        if self._is_target_url(url) or self._is_technical_url(url):
            return 0
        shared_path_bonus = self._shared_path_bonus(url)
        url_terms = set(extract_url_terms(url))
        if not self._has_url_level_semantic_fit(url_terms, shared_path_bonus=shared_path_bonus):
            return 0
        overlap_score = self._weighted_overlap_score(url_terms)
        if overlap_score == 0:
            return shared_path_bonus
        coverage_bonus = 0
        if self._target_term_weights:
            coverage_bonus = round(overlap_score / self._target_total_weight * 20)
        return (
            score_link(url, "", self._target.priority_terms)
            + overlap_score * 3
            + coverage_bonus
            + shared_path_bonus
        )

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
        if not any((shared_path_bonus, overlap_score, branch_score, core_branch_score, signature_score, legacy_score)):
            return 0
        if (
            shared_path_bonus < 10
            and overlap_score < 4
            and branch_score == 0
            and core_branch_score == 0
            and signature_score == 0
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

    def _candidate_score(self, snapshot: CrawledPageSnapshot) -> int:
        thematic_score = self._thematic_score(snapshot)
        proximity_score = 0 if snapshot.depth is None else max(self._good_depth_threshold - snapshot.depth, 0) * 5
        return thematic_score + proximity_score

    def _soft_candidate_score(self, snapshot: CrawledPageSnapshot) -> int:
        metadata_terms = snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms
        candidate_terms = metadata_terms | snapshot.body_terms
        overlap_score = self._weighted_overlap_score(candidate_terms)
        branch_score = self._weighted_overlap_score(self._branch_overlap_terms(metadata_terms))
        core_branch_score = self._weighted_overlap_score(self._core_branch_overlap_terms(metadata_terms))
        signature_score = self._weighted_overlap_score(self._signature_overlap_terms(candidate_terms))
        shared_path_bonus = self._shared_path_bonus(snapshot.url)
        legacy_score = score_link(snapshot.url, snapshot.title, self._target.priority_terms)
        if not any((overlap_score, branch_score, core_branch_score, signature_score, shared_path_bonus, legacy_score)):
            return 0
        phrase_bonus = 0
        if self._normalized_target_title:
            if self._normalized_target_title in snapshot.normalized_title:
                phrase_bonus += 16
            elif self._normalized_target_title in snapshot.normalized_h1:
                phrase_bonus += 12
            elif self._normalized_target_title in snapshot.normalized_text:
                phrase_bonus += 8
        if (
            shared_path_bonus < 10
            and overlap_score < 4
            and branch_score == 0
            and core_branch_score == 0
            and signature_score == 0
            and phrase_bonus == 0
        ):
            return 0
        depth_bonus = max(3 - snapshot.depth + 1, 0) * 3
        return (
            overlap_score * 2
            + branch_score * 3
            + core_branch_score * 4
            + signature_score * 2
            + shared_path_bonus
            + legacy_score
            + phrase_bonus
            + depth_bonus
        )

    def _thematic_score(self, snapshot: CrawledPageSnapshot) -> int:
        url_overlap = self._weighted_overlap_score(snapshot.url_terms)
        title_overlap = self._weighted_overlap_score(snapshot.title_terms)
        h1_overlap = self._weighted_overlap_score(snapshot.h1_terms)
        body_overlap = self._weighted_overlap_score(snapshot.body_terms)
        overlap_total = self._weighted_overlap_score(
            snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms | snapshot.body_terms
        )
        phrase_bonus = 0
        if self._normalized_target_title:
            if self._normalized_target_title in snapshot.normalized_title:
                phrase_bonus += 30
            elif self._normalized_target_title in snapshot.normalized_h1:
                phrase_bonus += 24
            elif self._normalized_target_title in snapshot.normalized_text:
                phrase_bonus += 18
        if self._normalized_target_text and self._normalized_target_text in snapshot.normalized_text:
            phrase_bonus += 14
        coverage_bonus = 0
        if self._target_term_weights:
            coverage_bonus = round(overlap_total / self._target_total_weight * 18)
        shared_path_bonus = self._shared_path_bonus(snapshot.url)
        metadata_terms = snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms
        core_branch_bonus = self._weighted_overlap_score(self._core_branch_overlap_terms(metadata_terms))
        cluster_penalty = 0
        if (
            self._target_core_branch_terms
            and self._has_branch_affinity(metadata_terms)
            and not self._has_core_branch_affinity(metadata_terms)
            and shared_path_bonus < 10
        ):
            cluster_penalty = 10
        legacy_score = score_link(snapshot.url, snapshot.title, self._target.priority_terms)
        return (
            phrase_bonus
            + title_overlap * 2
            + h1_overlap * 2
            + url_overlap * 2
            + body_overlap
            + coverage_bonus
            + core_branch_bonus * 2
            + shared_path_bonus
            + legacy_score
            - cluster_penalty
        )

    def _context_overlap_score(self, snapshot: CrawledPageSnapshot) -> int:
        return self._weighted_overlap_score(snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms)

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

    def _has_sufficient_term_overlap(self, candidate_terms: set[str] | frozenset[str]) -> bool:
        overlap = self._overlapping_target_terms(candidate_terms)
        if not overlap:
            return False
        overlap_score = self._weighted_overlap_score(overlap)
        signature_overlap = overlap & self._target_signature_terms
        if not self._target_signature_terms or len(self._target_term_weights) <= 2:
            return overlap_score >= MIN_RECOMMENDATION_CONTEXT_SCORE
        if len(self._target_signature_terms) == 1:
            return bool(signature_overlap) and overlap_score >= MIN_RECOMMENDATION_CONTEXT_SCORE
        if len(signature_overlap) >= 2:
            return overlap_score >= MIN_RECOMMENDATION_CONTEXT_SCORE
        if len(overlap) >= 2 and signature_overlap:
            return overlap_score >= MIN_RECOMMENDATION_CONTEXT_SCORE
        return False

    def _has_branch_affinity(self, candidate_terms: set[str] | frozenset[str]) -> bool:
        if not self._target_branch_terms:
            return False
        branch_overlap = self._branch_overlap_terms(candidate_terms)
        if not branch_overlap:
            return False
        branch_score = self._weighted_overlap_score(branch_overlap)
        return branch_score >= MIN_BRANCH_CONTEXT_SCORE or len(branch_overlap) >= 2

    def _has_core_branch_affinity(self, candidate_terms: set[str] | frozenset[str]) -> bool:
        if not self._target_core_branch_terms:
            return False
        core_overlap = self._core_branch_overlap_terms(candidate_terms)
        if not core_overlap:
            return False
        core_score = self._weighted_overlap_score(core_overlap)
        return core_score >= MIN_CORE_BRANCH_SCORE or len(core_overlap) >= 1

    def _has_strong_topic_overlap(self, candidate_terms: set[str] | frozenset[str]) -> bool:
        signature_overlap = self._signature_overlap_terms(candidate_terms)
        if len(signature_overlap) >= MIN_STRONG_SIGNATURE_COUNT:
            return True
        if len(signature_overlap) >= 2:
            return self._weighted_overlap_score(signature_overlap) >= MIN_STRONG_TOPIC_SCORE
        return False

    def _has_supporting_metadata_overlap(self, candidate_terms: set[str] | frozenset[str]) -> bool:
        if self._has_core_branch_affinity(candidate_terms) or self._has_branch_affinity(candidate_terms):
            return True
        signature_overlap = self._signature_overlap_terms(candidate_terms)
        if len(signature_overlap) >= 2:
            return True
        overlap = self._overlapping_target_terms(candidate_terms)
        if not self._target_branch_terms and len(overlap) >= 2:
            return self._weighted_overlap_score(overlap) >= MIN_RECOMMENDATION_CONTEXT_SCORE
        return False

    def _has_url_level_semantic_fit(
        self,
        candidate_terms: set[str] | frozenset[str],
        *,
        shared_path_bonus: int,
    ) -> bool:
        if self._has_sufficient_term_overlap(candidate_terms):
            if not self._target_branch_terms:
                return True
            if self._has_core_branch_affinity(candidate_terms):
                return True
            if self._has_branch_affinity(candidate_terms) and shared_path_bonus >= 10:
                return True
            return self._has_strong_topic_overlap(candidate_terms)
        return shared_path_bonus >= 10 and self._has_branch_affinity(candidate_terms)

    def _has_exact_target_phrase(self, snapshot: CrawledPageSnapshot) -> bool:
        if not self._normalized_target_title:
            return False
        return (
            self._normalized_target_title in snapshot.normalized_title
            or self._normalized_target_title in snapshot.normalized_h1
            or self._normalized_target_title in snapshot.normalized_text
        )

    def _is_snapshot_recommendable(self, snapshot: CrawledPageSnapshot) -> bool:
        if not snapshot.is_indexable or snapshot.links_to_target:
            return False
        if self._is_technical_source(
            url=snapshot.url,
            normalized_title=snapshot.normalized_title,
            normalized_h1=snapshot.normalized_h1,
        ):
            return False
        if self._has_exact_target_phrase(snapshot):
            return True
        metadata_terms = snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms
        if self._has_sufficient_term_overlap(metadata_terms) or self._has_core_branch_affinity(metadata_terms):
            return True
        candidate_terms = metadata_terms | snapshot.body_terms
        if self._shared_path_bonus(snapshot.url) >= 10 and self._has_strong_topic_overlap(candidate_terms):
            return True
        if not self._has_supporting_metadata_overlap(metadata_terms):
            return False
        return self._has_strong_topic_overlap(candidate_terms)

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
