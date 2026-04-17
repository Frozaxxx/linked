from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.services.link_placement_models import (
    ANCHOR_LABEL_STOP_WORDS,
    ANCHOR_STOP_WORDS,
    MAX_BODY_TEXT,
    MAX_RECOMMENDATION_SOURCE_DEPTH,
    MAX_TERMS_PER_FIELD,
    MIN_RECOMMENDATION_SOURCE_DEPTH,
    TEXT_TOKEN_RE,
    CrawledPageSnapshot,
    PlacementRecommendation,
    RankedRecommendation,
)
from app.services.matcher import (
    GENERIC_URL_TERMS,
    SIGNATURE_STOP_TERMS,
    extract_terms,
    extract_url_terms,
    normalize_text,
)


class LinkPlacementTextMixin:
    def _build_soft_relevance_reason(self, snapshot: CrawledPageSnapshot) -> str:
        metadata_terms = snapshot.url_terms | snapshot.title_terms | snapshot.h1_terms
        title_h1_overlap = list(self._overlapping_target_terms(snapshot.title_terms | snapshot.h1_terms))
        if title_h1_overlap:
            return f"Совпадение в title/H1 проверенной страницы по терминам: {', '.join(sorted(title_h1_overlap)[:4])}."
        metadata_overlap = list(self._overlapping_target_terms(metadata_terms))
        if metadata_overlap:
            return f"Совпадение в URL/title/H1 проверенной страницы по терминам: {', '.join(sorted(metadata_overlap)[:4])}."
        overlap = list(self._overlapping_target_terms(metadata_terms | snapshot.body_terms))
        if overlap:
            return f"Совпадение на проверенной странице по терминам: {', '.join(sorted(overlap)[:4])}."
        if self._shared_path_bonus(snapshot.url) > 0:
            return "Проверенная страница из соседнего раздела сайта, которую можно использовать как рабочего донора."
        return "Проверенная страница, которую можно использовать как рабочего донора по мягкой семантической оценке."

    def _build_soft_url_only_reason(self, url: str) -> str:
        url_overlap = list(set(extract_url_terms(url)) & self._target_terms)
        if url_overlap:
            return f"Совпадение по проверенному URL: {', '.join(sorted(url_overlap)[:4])}."
        if self._shared_path_bonus(url) > 0:
            return "Проверенный URL из соседнего раздела сайта, который можно использовать как рабочего донора."
        return "Проверенный URL, который можно использовать как рабочего донора по мягкой семантической оценке."

    def _anchor_hint(self) -> str | None:
        variants = self._anchor_variants()
        return "; ".join(variants[:3]) if variants else None

    def _anchor_variants(self) -> list[str]:
        variants: list[str] = []
        seen: set[str] = set()

        def add(value: str | None) -> None:
            if not value:
                return
            cleaned = re.sub(r"\s+", " ", value).strip(" -_,.;:")
            if len(cleaned) < 3:
                return
            if cleaned and cleaned[0].isalpha():
                cleaned = cleaned[0].upper() + cleaned[1:]
            normalized = cleaned.casefold()
            if normalized in seen:
                return
            seen.add(normalized)
            variants.append(cleaned)

        base_title = self._target.title.strip() if self._target.title else None
        add(base_title)
        if not base_title and self._target.url:
            target_parts = self._path_parts(self._target.url)
            if target_parts:
                base_title = target_parts[-1].replace("-", " ").replace("_", " ").strip()
                add(base_title)
        short_topic, secondary_topic = self._anchor_topic_phrases(base_title)
        branch_label = self._target_branch_label()
        core_label = self._target_core_label()
        if short_topic and branch_label and branch_label.casefold() not in (base_title or "").casefold():
            add(f"{short_topic} in the {branch_label}" if self._looks_latin_phrase(short_topic) and self._looks_latin_phrase(branch_label) else f"{branch_label} {short_topic}")
        if secondary_topic and short_topic:
            add(f"{secondary_topic} for {short_topic}" if self._looks_latin_phrase(short_topic) and self._looks_latin_phrase(secondary_topic) else f"{secondary_topic} {short_topic}")
        if core_label and short_topic and core_label.casefold() not in (base_title or "").casefold():
            add(f"{core_label} {short_topic} project" if self._looks_latin_phrase(core_label) and self._looks_latin_phrase(short_topic) else f"{core_label} {short_topic}")
        if not variants:
            target_terms = list(self._target.priority_terms[:4])
            if target_terms:
                add(" ".join(target_terms))
        return variants

    @staticmethod
    def _anchor_topic_phrases(base_title: str | None) -> tuple[str | None, str | None]:
        if not base_title:
            return None, None
        filtered_tokens: list[str] = []
        for token in TEXT_TOKEN_RE.findall(base_title):
            if len(token) < 2 or token.casefold() in ANCHOR_STOP_WORDS:
                continue
            filtered_tokens.append(token)
        if not filtered_tokens:
            return base_title, None
        short_topic = " ".join(filtered_tokens[:2]) if len(filtered_tokens) >= 2 else filtered_tokens[0]
        if len(filtered_tokens) >= 5:
            return short_topic, " ".join(filtered_tokens[2:5])
        if len(filtered_tokens) >= 4:
            return short_topic, " ".join(filtered_tokens[1:4])
        return short_topic, None

    def _target_core_label(self) -> str | None:
        if not self._target.url:
            return None
        for part in reversed(self._path_parts(self._target.url)[:-1]):
            label = self._clean_path_part_label(part)
            if label:
                return label
        return None

    def _target_branch_label(self) -> str | None:
        if not self._target.url:
            return None
        core_label = (self._target_core_label() or "").casefold()
        for part in reversed(self._path_parts(self._target.url)[:-1]):
            label = self._clean_path_part_label(part)
            if not label or label.casefold() == core_label:
                continue
            if len(label.split()) >= 2:
                return label
        return None

    @staticmethod
    def _clean_path_part_label(part: str) -> str | None:
        words: list[str] = []
        for token in TEXT_TOKEN_RE.findall(part.replace("_", "-").replace("-", " ")):
            lowered = token.casefold()
            if lowered.isdigit() or lowered in ANCHOR_LABEL_STOP_WORDS:
                continue
            if lowered in GENERIC_URL_TERMS or lowered in SIGNATURE_STOP_TERMS:
                continue
            if len(token) < 3 and not token.isupper():
                continue
            normalized = token.upper() if token.isupper() else token.capitalize()
            if normalized not in words:
                words.append(normalized)
        return " ".join(words) if words else None

    @staticmethod
    def _looks_latin_phrase(value: str) -> bool:
        return bool(value) and all(ord(char) < 128 for char in value if char.isalpha() or char.isspace())

    @staticmethod
    def _path_parts(url: str) -> list[str]:
        return [part for part in urlsplit(url).path.split("/") if part]

    def _estimated_structural_depth(self, url: str) -> int | None:
        parts = self._path_parts(url)
        if not parts:
            return 0
        depth = len(parts)
        return depth if depth >= 0 else None

    @staticmethod
    def build_snapshot(
        *,
        url: str,
        title: str,
        h1: str = "",
        depth: int | None,
        text: str,
        is_indexable: bool = True,
        links_to_target: bool = False,
    ) -> CrawledPageSnapshot:
        return CrawledPageSnapshot(
            url=url,
            title=title,
            depth=depth,
            normalized_title=normalize_text(title),
            normalized_h1=normalize_text(h1),
            normalized_text=normalize_text(text[:MAX_BODY_TEXT]),
            url_terms=frozenset(extract_url_terms(url)[:MAX_TERMS_PER_FIELD]),
            title_terms=frozenset(extract_terms(title)[:MAX_TERMS_PER_FIELD]),
            h1_terms=frozenset(extract_terms(h1)[:MAX_TERMS_PER_FIELD]),
            body_terms=frozenset(extract_terms(text[:MAX_BODY_TEXT])[:MAX_TERMS_PER_FIELD]),
            is_indexable=is_indexable,
            links_to_target=links_to_target,
        )

    @staticmethod
    def _remember_candidate(ranked: dict[str, RankedRecommendation], recommendation: PlacementRecommendation, score: int) -> None:
        existing = ranked.get(recommendation.source_url)
        if existing is None or score > existing.score:
            ranked[recommendation.source_url] = RankedRecommendation(recommendation=recommendation, score=score)

    @staticmethod
    def _projected_steps(depth: int | None) -> int | None:
        return None if depth is None else depth + 1

    @staticmethod
    def _is_allowed_source_depth(depth: int | None) -> bool:
        return depth is not None and MIN_RECOMMENDATION_SOURCE_DEPTH <= depth <= MAX_RECOMMENDATION_SOURCE_DEPTH

    @staticmethod
    def _placement_hint(depth: int | None) -> str:
        if depth is None:
            return "Лучше разместить ссылку в основном контенте страницы или в блоке связанных материалов."
        if depth == 0:
            return "Лучше поставить ссылку в заметный блок на главной или в верхнюю навигацию раздела."
        if depth <= 2:
            return "Лучше разместить ссылку в основном контенте страницы или в блоке связанных материалов."
        return "Лучше поставить ссылку ближе к началу основного контента или в списке связанных страниц."
