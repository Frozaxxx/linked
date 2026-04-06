from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.services.link_placement import PlacementRecommendation

if TYPE_CHECKING:
    from app.services.llm_summary import AnalysisMessageContext


RECOMMENDATION_MARKERS = (
    "Рекоменд",
    "Добавьте прямую ссылку",
    "Добавьте ссылку",
    "Лучше всего поставить",
    "Лучше поставить",
    "Разместите ссылку",
)


def build_static_message(context: AnalysisMessageContext) -> str | None:
    if context.optimization_status == "good" and context.steps_to_target is not None:
        return (
            "Перелинковка выглядит хорошей: "
            f"целевая страница находится за {context.steps_to_target} {step_word(context.steps_to_target)} "
            f"при пороге {context.good_depth_threshold}."
        )

    if context.placement_recommendations:
        if should_render_multiple_candidates(context):
            return build_soft_candidates_message(context)
        return build_single_candidate_message(context)

    if has_site_access_issue(context):
        return build_access_issue_message(context)

    return None


def build_fallback_message(context: AnalysisMessageContext) -> str:
    if context.placement_recommendations:
        if should_render_multiple_candidates(context):
            return build_soft_candidates_message(context)
        return build_single_candidate_message(context)

    if has_site_access_issue(context):
        return build_access_issue_message(context)

    message = problem_intro(context)
    return (
        f"{message} Стоит усилить ссылки с тематически близких разделов и материалов, "
        "чтобы сократить путь до цели и сделать страницу заметнее для пользователя и поисковых систем."
    )


def finalize_message(message: str, context: AnalysisMessageContext) -> str:
    cleaned = strip_model_recommendation_section(message, context)
    if not context.placement_recommendations:
        return cleaned

    if should_render_multiple_candidates(context):
        return append_soft_candidates_message(cleaned, context)

    best = context.placement_recommendations[0]
    if best.source_url in cleaned:
        return normalize_message(cleaned)

    return append_recommendation_sentence(cleaned, best)


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


def append_recommendation_sentence(message: str, recommendation: PlacementRecommendation) -> str:
    base = normalize_message(message)
    if base and base[-1] not in ".!?":
        base += "."

    sentence = recommendation_sentence(recommendation)
    if not base:
        return sentence
    return f"{base} {sentence}"


def problem_intro(context: AnalysisMessageContext) -> str:
    if context.found and context.steps_to_target is not None:
        return (
            "Перелинковка слабая: "
            f"до целевой страницы {context.steps_to_target} {step_word(context.steps_to_target)}, "
            f"а хороший уровень для проекта - не глубже {context.good_depth_threshold} {step_word_after_preposition(context.good_depth_threshold)}. "
            "Из-за длинного пути страница получает меньше внутреннего веса, пользователю сложнее быстро до нее дойти, "
            "а поисковому роботу сложнее понять, что страница важна внутри сайта."
        )

    return (
        "Перелинковка слабая: "
        f"короткий путь до целевой страницы в пределах {context.good_depth_threshold} {step_word_after_preposition(context.good_depth_threshold)} не подтвержден. "
        "Из-за этого страница получает меньше внутреннего веса, пользователю сложнее быстро найти ее из навигации и связанных материалов, "
        "а поисковым системам сложнее считать ее приоритетной."
    )


def build_single_candidate_message(context: AnalysisMessageContext) -> str:
    recommendation = context.placement_recommendations[0]
    parts = [
        problem_intro(context),
        f"Лучший семантически близкий URL для ссылки: {recommendation.source_url}.",
        recommendation.placement_hint.rstrip(".") + ".",
    ]
    if recommendation.projected_steps_to_target is not None:
        parts.append(
            f"После добавления ссылки путь до цели сократится до {recommendation.projected_steps_to_target} "
            f"{step_word_after_preposition(recommendation.projected_steps_to_target)}."
        )
    if recommendation.anchor_hint:
        parts.append(f"В анкоре можно использовать: {recommendation.anchor_hint}.")
    return " ".join(parts)


def should_render_multiple_candidates(context: AnalysisMessageContext) -> bool:
    return bool(context.placement_recommendations) and context.placement_recommendations[0].confidence in {"soft", "fallback"}


def build_soft_candidates_message(context: AnalysisMessageContext) -> str:
    return f"{problem_intro(context)} {soft_candidates_sentence(context.placement_recommendations)}"


def append_soft_candidates_message(message: str, context: AnalysisMessageContext) -> str:
    base = normalize_message(message)
    if not base:
        base = problem_intro(context)
    if base[-1] not in ".!?":
        base += "."
    return f"{base} {soft_candidates_sentence(context.placement_recommendations)}"


def soft_candidates_sentence(recommendations: list[PlacementRecommendation]) -> str:
    if recommendations and recommendations[0].confidence == "fallback":
        return fallback_candidates_sentence(recommendations)

    top_candidates = recommendations[:3]
    candidate_labels = [soft_candidate_label(recommendation) for recommendation in top_candidates]
    labels = "; ".join(candidate_labels)
    placement_hint = top_candidates[0].placement_hint.rstrip(".")
    return (
        "Сильного семантически точного донора сервис не подтвердил, поэтому ниже показаны до 3 проверенных кандидатов "
        f"в пределах 1-3 шагов от стартовой страницы: {labels}. "
        f"{placement_hint}. Это сократит путь до цели и даст пользователю более короткий и понятный маршрут."
    )


def fallback_candidates_sentence(recommendations: list[PlacementRecommendation]) -> str:
    top_candidates = recommendations[:3]
    candidate_labels = [soft_candidate_label(recommendation) for recommendation in top_candidates]
    labels = "; ".join(candidate_labels)
    placement_hint = top_candidates[0].placement_hint.rstrip(".")
    return (
        "Сервис не смог подтвердить путь обходом, поэтому ниже показаны до 3 резервных URL-кандидатов "
        f"из близкой структурной ветки цели на глубине 1-3 уровней: {labels}. "
        f"{placement_hint}. Эти варианты нужно проверить вручную на странице, но они лучше пустого ответа без кандидатов."
    )


def soft_candidate_label(recommendation: PlacementRecommendation) -> str:
    if recommendation.source_depth is None:
        return recommendation.source_url
    return f"{recommendation.source_url} ({recommendation.source_depth} {step_word(recommendation.source_depth)})"


def recommendation_sentence(recommendation: PlacementRecommendation) -> str:
    if recommendation.confidence == "soft":
        intro = (
            "Если сильный семантический донор не найден, как рабочий кандидат можно использовать URL "
            f"{recommendation.source_url}."
        )
    elif recommendation.confidence == "fallback":
        intro = (
            "Если более близкую страницу подтвердить не удалось, как запасной вариант можно использовать URL "
            f"{recommendation.source_url}."
        )
    elif recommendation.confidence == "medium":
        intro = f"Подходящий URL для прямой ссылки: {recommendation.source_url}."
    else:
        intro = f"Лучший URL для прямой ссылки: {recommendation.source_url}."

    parts = [intro, recommendation.placement_hint.rstrip(".") + "."]
    if recommendation.projected_steps_to_target is not None:
        parts.append(
            f"После добавления ссылки путь до цели сократится до {recommendation.projected_steps_to_target} "
            f"{step_word_after_preposition(recommendation.projected_steps_to_target)}."
        )
    if recommendation.anchor_hint:
        parts.append(f"В анкоре можно использовать: {recommendation.anchor_hint}.")
    return " ".join(parts)


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
        "Сначала нужно открыть доступ к HTML-страницам и sitemap, иначе сервис не сможет надежно подобрать страницу-донор."
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
