from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.services.link_placement import PlacementRecommendation

if TYPE_CHECKING:
    from app.services.llm_summary import AnalysisMessageContext


RECOMMENDATION_MARKERS = (
    "Рекоменду",
    "Добавьте прямую ссылку",
    "Добавьте ссылку",
    "Лучше всего поставить",
    "Лучше поставить",
    "Разместите ссылку",
)


def build_static_message(context: AnalysisMessageContext) -> str | None:
    if context.optimization_status == "good" and context.steps_to_target is not None:
        return build_good_message(context)

    if context.placement_recommendations:
        return build_soft_candidates_message(context)

    if has_site_access_issue(context):
        return build_access_issue_message(context)

    return None


def build_fallback_message(context: AnalysisMessageContext) -> str:
    if context.optimization_status == "good" and context.steps_to_target is not None:
        return build_good_message(context)

    if context.placement_recommendations:
        return build_soft_candidates_message(context)

    if has_site_access_issue(context):
        return build_access_issue_message(context)

    message = problem_intro(context)
    return (
        f"{message} Стоит усилить ссылки с тематически близких разделов и материалов, "
        "чтобы сократить путь до цели и сделать страницу заметнее для пользователя и поисковых систем."
    )


def finalize_message(message: str, context: AnalysisMessageContext) -> str:
    if context.optimization_status == "good" and context.steps_to_target is not None:
        return build_good_message(context)

    cleaned = strip_model_recommendation_section(message, context)
    if not context.placement_recommendations:
        return cleaned

    return append_soft_candidates_message(cleaned, context)


def strip_model_recommendation_section(message: str, context: AnalysisMessageContext) -> str:
    cleaned = message.strip()

    for recommendation in context.placement_recommendations:
        if recommendation.source_title:
            variants = (
                recommendation.source_title,
                f"«{recommendation.source_title}»",
                f'"{recommendation.source_title}"',
            )
            for variant in variants:
                cleaned = cleaned.replace(variant, "")

    marker_positions = [cleaned.find(marker) for marker in RECOMMENDATION_MARKERS if marker in cleaned]
    if marker_positions:
        cleaned = cleaned[: min(marker_positions)]

    return normalize_message(cleaned)


def crawl_is_inconclusive(context: AnalysisMessageContext) -> bool:
    return getattr(context, "budget_exhausted", False) or getattr(context, "level_truncated", False)


def problem_intro(context: AnalysisMessageContext) -> str:
    if context.found and context.steps_to_target is not None:
        if context.steps_to_target <= context.good_depth_threshold:
            return build_good_message(context)
        return (
            "Перелинковка слабая: "
            f"до целевой страницы {context.steps_to_target} {step_word(context.steps_to_target)}, "
            f"а хороший уровень для проекта - не глубже {context.good_depth_threshold} {step_word_after_preposition(context.good_depth_threshold)}. "
            "Из-за длинного пути страница получает меньше внутреннего веса, пользователю сложнее быстро до нее дойти, "
            "а поисковому роботу сложнее понять, что страница важна внутри сайта."
        )

    if crawl_is_inconclusive(context):
        parts: list[str] = []
        if context.found_in_sitemap:
            parts.append("Целевая страница есть в sitemap.")
        parts.append(
            f"Мы анализируем только страницы, достижимые от главной не более чем за {context.search_depth_limit} {step_word_after_preposition(context.search_depth_limit)}."
        )
        parts.append(
            "Целевая страница есть на сайте, но в эту область подтвержденно не попала или не была достигнута в рамках бюджета."
        )
        return " ".join(parts)

    return (
        "Перелинковка слабая: "
        f"короткий путь до целевой страницы в пределах {context.search_depth_limit} {step_word_after_preposition(context.search_depth_limit)} не подтвержден. "
        "Из-за этого страница получает меньше внутреннего веса, пользователю сложнее быстро найти ее из навигации и связанных материалов, "
        "а поисковым системам сложнее считать ее приоритетной."
    )


def build_good_message(context: AnalysisMessageContext) -> str:
    target_label = (
        "каноническая версия целевой страницы"
        if "canonical_url" in context.matched_by
        else "эквивалентная версия целевой страницы"
        if "equivalent_url" in context.matched_by
        else "целевая страница"
    )
    return (
        "Перелинковка хорошая: "
        f"{target_label} находится за {context.steps_to_target} {step_word(context.steps_to_target)} "
        f"при пороге {context.good_depth_threshold}. "
        "Пользователь быстро доберется до страницы, а поисковым системам легко увидеть ее место в структуре сайта."
    )


def build_soft_candidates_message(context: AnalysisMessageContext) -> str:
    return f"{problem_intro(context)} {soft_candidates_sentence(context)}"


def append_soft_candidates_message(message: str, context: AnalysisMessageContext) -> str:
    base = normalize_message(message)
    if not base:
        base = problem_intro(context)
    if base[-1] not in ".!?":
        base += "."
    return f"{base} {soft_candidates_sentence(context)}"


def soft_candidates_sentence(context: AnalysisMessageContext) -> str:
    recommendations = context.placement_recommendations
    top_candidates = recommendations[:3]
    candidate_labels = [
        soft_candidate_label(recommendation, index=index, context=context)
        for index, recommendation in enumerate(top_candidates, start=1)
    ]
    labels = "; ".join(candidate_labels)
    placement_hint = top_candidates[0].placement_hint.rstrip(".")
    return (
        "Ниже показаны до 3 URL-кандидатов по мягкой семантической оценке в порядке приоритета: "
        f"{labels}. {placement_hint}. Они отсортированы от более релевантного к менее надежному и требуют ручной проверки."
    )


def soft_candidate_label(
    recommendation: PlacementRecommendation,
    *,
    index: int | None = None,
    context: AnalysisMessageContext | None = None,
) -> str:
    prefix = f"{index}) " if index is not None else ""
    details: list[str] = []
    depth_label = candidate_depth_label(recommendation, context=context)
    if depth_label:
        details.append(depth_label)
    reason_label = candidate_reason_label(recommendation)
    if reason_label:
        details.append(reason_label)
    if not details:
        return f"{prefix}{recommendation.source_url}"
    return f"{prefix}{recommendation.source_url} ({'; '.join(details)})"


def candidate_depth_label(
    recommendation: PlacementRecommendation,
    *,
    context: AnalysisMessageContext | None = None,
) -> str | None:
    if recommendation.source_depth is None:
        return None
    if context is not None and crawl_is_inconclusive(context):
        return f"глубина в текущем запуске: {recommendation.source_depth}"
    return f"глубина: {recommendation.source_depth}"


def candidate_reason_label(recommendation: PlacementRecommendation) -> str | None:
    reason = normalize_message(recommendation.reason).rstrip(".")
    if not reason:
        return None
    if len(reason) > 110:
        reason = reason[:107].rstrip(" ,.;:") + "..."
    first_token = reason.split(maxsplit=1)[0]
    if len(first_token) > 1 and (first_token.isupper() or any(char.isdigit() for char in first_token)):
        return reason
    return reason[0].lower() + reason[1:] if len(reason) > 1 else reason.lower()


def has_site_access_issue(context: AnalysisMessageContext) -> bool:
    return (
        not context.found
        and context.pages_fetched == 0
        and context.pages_discovered <= 1
        and not context.path
        and not context.placement_recommendations
    )


def build_access_issue_message(context: AnalysisMessageContext) -> str:
    html_mode = html_fetch_mode_phrase(context.html_fetch_mode)
    sitemap_mode = sitemap_fetch_mode_phrase(context.sitemap_fetch_mode)
    return (
        "Перелинковка выглядит слабой, но сервис не смог подтвердить путь до цели: "
        f"{html_mode}, {sitemap_mode}, однако сайт не отдал достаточно данных для обхода. "
        "Из-за этого целевая страница может оставаться глубокой, получать меньше внутреннего веса и хуже находиться пользователем. "
        "Сначала нужно открыть доступ к HTML-страницам и sitemap, иначе сервис не сможет надежно подобрать страницу-донора."
    )


def html_fetch_mode_phrase(mode: str) -> str:
    if mode == "playwright":
        return "HTML-страницы запрашивались через Playwright"
    if mode == "http-only":
        return "HTML-страницы запрашивались только по HTTP"
    if mode == "mixed":
        return "HTML-страницы запрашивались смешанно: через Playwright и по HTTP"
    return "HTML-страницы не запрашивались"


def sitemap_fetch_mode_phrase(mode: str) -> str:
    if mode == "http-only":
        return "sitemap проверялся только по HTTP"
    if mode == "mixed":
        return "sitemap проверялся смешанно: через Playwright и по HTTP"
    if mode == "playwright":
        return "sitemap проверялся через Playwright"
    return "sitemap не проверялся"


def step_word(value: int) -> str:
    tail = value % 100
    if 11 <= tail <= 14:
        return "шагов"
    last = value % 10
    if last == 1:
        return "шаг"
    if 2 <= last <= 4:
        return "шага"
    return "шагов"


def step_word_after_preposition(value: int) -> str:
    tail = value % 100
    if tail % 10 == 1 and tail != 11:
        return "шага"
    return "шагов"


def normalize_message(message: str) -> str:
    normalized = re.sub(r"\s+", " ", message).strip()
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"([.]){2,}", ".", normalized)
    normalized = re.sub(r"\s+\.", ".", normalized)
    normalized = re.sub(r"\s+,", ",", normalized)
    return normalized.strip(" ,")
