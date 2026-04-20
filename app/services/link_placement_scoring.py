from __future__ import annotations

import re
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


OPAQUE_URL_TOKEN_RE = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z0-9]{12,}$")
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


class LinkPlacementScoringMixin:
    def score_source_url_soft(self, url: str) -> int:
        metrics = self._source_url_score_metrics(url)
        if not metrics or not self._passes_soft_semantic_gate(metrics):
            return 0
        return metrics["total_score"]

    def score_source_url_fallback(self, url: str) -> int | None:
        metrics = self._source_url_score_metrics(url)
        if not metrics or not self._has_fallback_signal(metrics):
            return None
        return metrics["total_score"]

    def _soft_candidate_score(self, snapshot: CrawledPageSnapshot) -> int:
        metadata_terms = snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms
        title_h1_terms = snapshot.title_terms | snapshot.h1_terms
        candidate_terms = metadata_terms | snapshot.body_terms
        overlap_terms = self._overlapping_target_terms(candidate_terms)
        metadata_overlap_terms = self._overlapping_target_terms(metadata_terms)
        title_h1_overlap_terms = self._overlapping_target_terms(title_h1_terms)
        branch_terms = self._branch_overlap_terms(metadata_terms)
        core_branch_terms = self._core_branch_overlap_terms(metadata_terms)
        signature_terms = self._signature_overlap_terms(candidate_terms)
        overlap_score = self._weighted_overlap_score(overlap_terms)
        branch_score = self._weighted_overlap_score(branch_terms)
        core_branch_score = self._weighted_overlap_score(core_branch_terms)
        signature_score = self._weighted_overlap_score(signature_terms)
        title_h1_score = self._weighted_overlap_score(title_h1_overlap_terms)
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
        url_shape_metrics = self._url_shape_metrics(snapshot.url)
        metrics = {
            **url_shape_metrics,
            "shared_path_bonus": shared_path_bonus,
            "overlap_score": overlap_score,
            "branch_score": branch_score,
            "core_branch_score": core_branch_score,
            "signature_score": signature_score,
            "title_h1_score": title_h1_score,
            "phrase_bonus": phrase_bonus,
            "overlap_terms_count": len(overlap_terms),
            "metadata_overlap_terms_count": len(metadata_overlap_terms),
            "title_h1_overlap_terms_count": len(title_h1_overlap_terms),
            "branch_terms_count": len(branch_terms),
            "core_branch_terms_count": len(core_branch_terms),
            "signature_terms_count": len(signature_terms),
            "total_score": (
                overlap_score * 3
                + branch_score * 4
                + core_branch_score * 5
                + signature_score * 3
                + title_h1_score * 2
                + shared_path_bonus
                + url_shape_metrics["date_path_score"]
                + url_shape_metrics["target_parent_bonus"]
                + legacy_score
                + phrase_bonus
            ),
        }
        if not self._passes_soft_semantic_gate(metrics):
            return 0
        return metrics["total_score"]

    def _fallback_candidate_score(self, snapshot: CrawledPageSnapshot) -> int | None:
        metadata_terms = snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms
        title_h1_terms = snapshot.title_terms | snapshot.h1_terms
        candidate_terms = metadata_terms | snapshot.body_terms
        overlap_terms = self._overlapping_target_terms(candidate_terms)
        metadata_overlap_terms = self._overlapping_target_terms(metadata_terms)
        title_h1_overlap_terms = self._overlapping_target_terms(title_h1_terms)
        branch_terms = self._branch_overlap_terms(metadata_terms)
        core_branch_terms = self._core_branch_overlap_terms(metadata_terms)
        signature_terms = self._signature_overlap_terms(candidate_terms)
        overlap_score = self._weighted_overlap_score(overlap_terms)
        branch_score = self._weighted_overlap_score(branch_terms)
        core_branch_score = self._weighted_overlap_score(core_branch_terms)
        signature_score = self._weighted_overlap_score(signature_terms)
        title_h1_score = self._weighted_overlap_score(title_h1_overlap_terms)
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
        url_shape_metrics = self._url_shape_metrics(snapshot.url)
        metrics = {
            **url_shape_metrics,
            "shared_path_bonus": shared_path_bonus,
            "overlap_score": overlap_score,
            "branch_score": branch_score,
            "core_branch_score": core_branch_score,
            "signature_score": signature_score,
            "title_h1_score": title_h1_score,
            "phrase_bonus": phrase_bonus,
            "overlap_terms_count": len(overlap_terms),
            "metadata_overlap_terms_count": len(metadata_overlap_terms),
            "title_h1_overlap_terms_count": len(title_h1_overlap_terms),
            "branch_terms_count": len(branch_terms),
            "core_branch_terms_count": len(core_branch_terms),
            "signature_terms_count": len(signature_terms),
            "total_score": (
                overlap_score * 3
                + branch_score * 4
                + core_branch_score * 5
                + signature_score * 3
                + title_h1_score * 2
                + shared_path_bonus
                + url_shape_metrics["date_path_score"]
                + url_shape_metrics["target_parent_bonus"]
                + legacy_score
                + phrase_bonus
            ),
        }
        if not self._has_fallback_signal(metrics):
            return None
        return metrics["total_score"]

    @staticmethod
    def _passes_soft_semantic_gate(metrics: dict[str, int]) -> bool:
        semantic_score = (
            metrics["overlap_score"]
            + metrics["branch_score"]
            + metrics["core_branch_score"]
            + metrics["signature_score"]
            + metrics.get("title_h1_score", 0)
            + metrics.get("phrase_bonus", 0)
        )
        if metrics.get("target_parent_bonus", 0) > 0:
            return LinkPlacementScoringMixin._has_primary_thematic_signal(metrics)
        if metrics.get("phrase_bonus", 0) >= 8:
            return True
        if LinkPlacementScoringMixin._is_weak_news_article_shape(metrics):
            return False
        if (
            metrics["core_branch_terms_count"] >= 1
            and not metrics.get("target_has_date_path", 0)
            and LinkPlacementScoringMixin._has_primary_thematic_signal(metrics)
        ):
            return True
        if metrics["overlap_terms_count"] < 2:
            return False
        if metrics["signature_terms_count"] >= 2 and semantic_score >= 14:
            return True
        if metrics["signature_terms_count"] >= 1 and metrics["branch_terms_count"] >= 1 and semantic_score >= 12:
            return True
        if (
            metrics["signature_terms_count"] >= 1
            and metrics.get("title_h1_overlap_terms_count", 0) >= 1
            and semantic_score >= 12
        ):
            return True
        if (
            metrics.get("title_h1_overlap_terms_count", 0) >= 2
            and metrics["signature_terms_count"] >= 1
            and semantic_score >= 10
        ):
            return True
        if (
            metrics["metadata_overlap_terms_count"] >= 2
            and metrics["signature_terms_count"] >= 1
            and semantic_score >= 10
        ):
            return True
        if metrics["overlap_terms_count"] >= 3 and semantic_score >= 12:
            return True
        return (
            metrics["shared_path_bonus"] >= 15
            and metrics["overlap_terms_count"] >= 2
            and LinkPlacementScoringMixin._has_primary_thematic_signal(metrics)
            and semantic_score >= 8
        )

    @staticmethod
    def _has_fallback_signal(metrics: dict[str, int]) -> bool:
        if metrics.get("target_parent_bonus", 0) > 0:
            return True
        if LinkPlacementScoringMixin._is_weak_news_article_shape(metrics):
            return False
        if metrics.get("phrase_bonus", 0) > 0:
            return True
        if metrics["core_branch_terms_count"] > 0 and not metrics.get("target_has_date_path", 0):
            return True
        if metrics["overlap_terms_count"] < 2:
            return False
        return bool(
            metrics["signature_terms_count"] >= 2
            or (metrics["signature_terms_count"] >= 1 and metrics["branch_terms_count"] >= 1)
            or (
                metrics["signature_terms_count"] >= 1
                and metrics.get("title_h1_overlap_terms_count", 0) >= 1
            )
            or (
                metrics.get("title_h1_overlap_terms_count", 0) >= 2
                and metrics["signature_terms_count"] >= 1
            )
            or (metrics["metadata_overlap_terms_count"] >= 2 and metrics["signature_terms_count"] >= 1)
            or (metrics["shared_path_bonus"] >= 10 and metrics["overlap_terms_count"] >= 2)
        )

    @staticmethod
    def _has_primary_thematic_signal(metrics: dict[str, int]) -> bool:
        return bool(
            metrics.get("phrase_bonus", 0) >= 8
            or metrics["signature_terms_count"] >= 1
            or (
                metrics.get("title_h1_overlap_terms_count", 0) >= 2
                and metrics["overlap_terms_count"] >= 3
            )
        )

    def _source_url_score_metrics(self, url: str) -> dict[str, int] | None:
        if self._is_target_url(url) or self._is_technical_url(url):
            return None
        shared_path_bonus = self._shared_path_bonus(url)
        url_terms = set(extract_url_terms(url))
        overlap_terms = self._overlapping_target_terms(url_terms)
        branch_terms = self._branch_overlap_terms(url_terms)
        core_branch_terms = self._core_branch_overlap_terms(url_terms)
        signature_terms = self._signature_overlap_terms(url_terms)
        overlap_score = self._weighted_overlap_score(overlap_terms)
        branch_score = self._weighted_overlap_score(branch_terms)
        core_branch_score = self._weighted_overlap_score(core_branch_terms)
        signature_score = self._weighted_overlap_score(signature_terms)
        legacy_score = score_link(url, "", self._target.priority_terms)
        url_shape_metrics = self._url_shape_metrics(url)
        return {
            **url_shape_metrics,
            "shared_path_bonus": shared_path_bonus,
            "overlap_score": overlap_score,
            "branch_score": branch_score,
            "core_branch_score": core_branch_score,
            "signature_score": signature_score,
            "title_h1_score": 0,
            "overlap_terms_count": len(overlap_terms),
            "metadata_overlap_terms_count": len(overlap_terms),
            "title_h1_overlap_terms_count": 0,
            "branch_terms_count": len(branch_terms),
            "core_branch_terms_count": len(core_branch_terms),
            "signature_terms_count": len(signature_terms),
            "total_score": (
                overlap_score * 3
                + branch_score * 4
                + core_branch_score * 5
                + signature_score * 3
                + shared_path_bonus
                + url_shape_metrics["date_path_score"]
                + url_shape_metrics["target_parent_bonus"]
                + legacy_score
            ),
        }

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

    @staticmethod
    def _is_weak_news_article_shape(metrics: dict[str, int]) -> bool:
        if not metrics.get("target_has_opaque_signature", 0) and not metrics.get("target_has_date_path", 0):
            return False
        if metrics.get("target_parent_bonus", 0) > 0:
            return False
        if metrics.get("source_has_opaque_signature", 0) and metrics["signature_terms_count"] == 0:
            return True
        if (
            metrics.get("target_has_date_path", 0)
            and metrics.get("source_has_date_path", 0)
            and metrics.get("shared_date_parts_count", 0) == 0
        ):
            return True
        return False

    def _url_shape_metrics(self, source_url: str) -> dict[str, int]:
        target_parts = self._path_parts(self._target.url or "")
        source_parts = self._path_parts(source_url)
        target_numeric_parts = self._numeric_path_parts(target_parts)
        source_numeric_parts = self._numeric_path_parts(source_parts)
        target_has_date_path = self._has_date_path(target_numeric_parts)
        target_has_opaque_signature = self._has_opaque_path_token(target_parts)
        shared_date_parts = set(target_numeric_parts) & set(source_numeric_parts)
        source_is_parent = bool(
            target_parts
            and source_parts
            and (len(source_parts) >= 2 or (target_has_date_path and target_has_opaque_signature))
            and len(source_parts) < len(target_parts)
            and target_parts[: len(source_parts)] == source_parts
        )
        return {
            "target_has_date_path": int(target_has_date_path),
            "source_has_date_path": int(self._has_date_path(source_numeric_parts)),
            "shared_date_parts_count": len(shared_date_parts),
            "date_path_score": len(shared_date_parts) * 18,
            "target_parent_bonus": 80 if source_is_parent else 0,
            "target_has_opaque_signature": int(target_has_opaque_signature),
            "source_has_opaque_signature": int(self._has_opaque_path_token(source_parts)),
        }

    @staticmethod
    def _numeric_path_parts(parts: list[str]) -> list[str]:
        return [part for part in parts if part.isdigit()]

    @staticmethod
    def _has_date_path(numeric_parts: list[str]) -> bool:
        if not any(YEAR_RE.match(part) for part in numeric_parts):
            return False
        return len(numeric_parts) >= 3

    @staticmethod
    def _has_opaque_path_token(parts: list[str]) -> bool:
        return any(OPAQUE_URL_TOKEN_RE.match(part.casefold()) is not None for part in parts)
