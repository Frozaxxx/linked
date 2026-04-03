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
            f"целевая страница находится за {context.steps_to_target} шаг(а/ов) "
            f"при пороге {context.good_depth_threshold}."
        )

    if context.placement_recommendations and not has_site_access_issue(context):
        if should_render_multiple_candidates(context):
            return build_soft_candidates_message(context)
        return build_single_candidate_message(context)

    if should_render_multiple_candidates(context):
        return build_soft_candidates_message(context)

    if (
        not context.found
        and context.pages_fetched == 0
        and context.pages_discovered <= 1
        and not context.path
        and context.placement_recommendations
    ):
        return append_recommendation_sentence(
            (
                "Не удалось получить страницы сайта или sitemap, поэтому сервис не смог "
                "надежно проверить путь до цели."
            ),
            context.placement_recommendations[0],
        )

    if has_site_access_issue(context):
        return (
            "Не удалось получить страницы сайта или sitemap, поэтому сервис не смог определить путь до цели "
            "и не смог подобрать страницу для размещения ссылки. Часто это связано с защитой сайта от "
            "автоматических запросов. Сначала нужно открыть доступ к страницам, иначе усилить внутренние "
            "ссылки по факту нечем."
        )

    return None


def build_fallback_message(context: AnalysisMessageContext) -> str:
    if should_render_multiple_candidates(context):
        return build_soft_candidates_message(context)

    if has_site_access_issue(context):
        return (
            "Не удалось получить страницы сайта или sitemap, поэтому сервис не смог определить путь до цели "
            "и не смог подобрать страницу для размещения ссылки. Часто это связано с защитой сайта от "
            "автоматических запросов. Сначала нужно открыть доступ к страницам, иначе усилить внутренние "
            "ссылки по факту нечем."
        )

    if (
        not context.found
        and context.pages_fetched == 0
        and context.pages_discovered <= 1
        and not context.path
        and context.placement_recommendations
    ):
        return append_recommendation_sentence(
            (
                "Не удалось получить страницы сайта или sitemap, поэтому сервис не смог "
                "надежно проверить путь до цели."
            ),
            context.placement_recommendations[0],
        )

    if context.found and context.steps_to_target is not None:
        message = (
            "Перелинковка слабая: "
            f"целевая страница найдена только за {context.steps_to_target} шаг(а/ов), "
            f"это выше порога {context.good_depth_threshold}. "
            "Из-за длинного пути страница получает меньше внутреннего веса и до нее "
            "сложнее добраться пользователю и поисковому роботу."
        )
    else:
        message = (
            "Перелинковка слабая: "
            f"до целевой страницы не удалось дойти в пределах {context.good_depth_threshold} шаг(а/ов). "
            "Это означает, что текущая внутренняя перелинковка не дает короткого и очевидного пути к цели."
        )

    if not context.placement_recommendations:
        return (
            f"{message} Стоит усилить внутренние ссылки с релевантных страниц и разделов сайта."
        )

    return append_recommendation_sentence(message, context.placement_recommendations[0])


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
            "На целевой странице плохое линкование: "
            f"до целевой страницы {context.steps_to_target} шагов, "
            f"что больше порога {context.good_depth_threshold}. "
            "Это значит, что пользователь в редком случае дойдет до нее, "
            "а поисковым системам сложнее передавать ей внутренний вес."
        )

    return (
        "На целевой странице плохое линкование: "
        f"до целевой страницы больше {context.good_depth_threshold} шагов "
        "или короткий путь не найден. Это значит, что пользователь в редком случае "
        "дойдет до нее, что плохо и для удобства, и для SEO."
    )


def build_single_candidate_message(context: AnalysisMessageContext) -> str:
    recommendation = context.placement_recommendations[0]
    parts = [
        problem_intro(context),
        (
            "Чтобы исправить данную ситуацию, советую вставить прямую ссылку или ссылку "
            f"в контексте на целевую со страницы {recommendation.source_url}."
        ),
        recommendation.placement_hint.rstrip(".") + ".",
    ]
    if recommendation.anchor_hint:
        parts.append(f"В анкоре можно использовать: {recommendation.anchor_hint}.")
    return " ".join(parts)


def should_render_multiple_candidates(context: AnalysisMessageContext) -> bool:
    return bool(context.placement_recommendations) and context.placement_recommendations[0].confidence == "soft"


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
    candidate_labels = [soft_candidate_label(recommendation) for recommendation in recommendations[:3]]
    labels = "; ".join(candidate_labels)
    placement_hint = recommendations[0].placement_hint.rstrip(".")
    return (
        "Чтобы исправить данную ситуацию, советую вставить прямую ссылку или ссылку в контексте "
        "на целевую с одной из этих страниц. Программа не смогла найти сильного семантически "
        "точного донора, поэтому фильтр был ослаблен и ниже приведены резервные варианты: "
        f"{labels}. Все URL подтверждены обходом и доступны от стартовой страницы не глубже 3 шагов, "
        "поэтому можно выбрать наиболее подходящий по контексту вариант. "
        f"{placement_hint}."
    )


def soft_candidate_label(recommendation: PlacementRecommendation) -> str:
    if recommendation.source_depth is None:
        return recommendation.source_url
    step_word = "шаг"
    if recommendation.source_depth not in {1, 21, 31}:
        step_word = "шага" if recommendation.source_depth not in {5, 6, 7, 8, 9, 10, 11, 12, 13, 14} else "шагов"
    return f"{recommendation.source_url} ({recommendation.source_depth} {step_word})"


def recommendation_sentence(recommendation: PlacementRecommendation) -> str:
    if recommendation.confidence == "soft":
        intro = (
            "Если точную тематически близкую страницу подобрать не удалось, как рабочий вариант "
            f"можно поставить прямую ссылку со страницы {recommendation.source_url}."
        )
    elif recommendation.confidence == "fallback":
        intro = (
            "Если более близкую страницу подтвердить не удалось, как запасной вариант можно "
            f"поставить прямую ссылку со стартовой страницы {recommendation.source_url}."
        )
    elif recommendation.confidence == "medium":
        intro = f"Рекомендованная страница для прямой ссылки: {recommendation.source_url}."
    else:
        intro = f"Лучше всего поставить прямую ссылку со страницы {recommendation.source_url}."

    parts = [intro, recommendation.placement_hint.rstrip(".") + "."]
    if recommendation.projected_steps_to_target is not None:
        if recommendation.confidence in {"soft", "fallback"}:
            parts.append(
                f"Это поможет сократить путь до {recommendation.projected_steps_to_target} шаг(а/ов)."
            )
        else:
            parts.append(
                f"Это сократит путь до {recommendation.projected_steps_to_target} шаг(а/ов)."
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


def normalize_message(message: str) -> str:
    normalized = re.sub(r"\s+", " ", message).strip()
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"([.]){2,}", ".", normalized)
    normalized = re.sub(r"\s+\.", ".", normalized)
    normalized = re.sub(r"\s+,", ",", normalized)
    return normalized.strip(" ,")
