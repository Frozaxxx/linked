from __future__ import annotations

import json
import logging
from typing import Any

from app.models import AnalysisMessageContext, GeneratedAnalysisMessage
from app.schemas import OptimizationStatus
from app.services.gigachat_client import create_gigachat_client
from app.services.link_placement import PlacementRecommendation
from app.services.llm_summary_templates import (
    append_soft_candidates_message,
    build_fallback_message,
    build_soft_candidates_message,
    build_static_message,
    finalize_message,
    has_site_access_issue,
    normalize_message,
    problem_intro,
    should_render_multiple_candidates,
    soft_candidate_label,
    soft_candidates_sentence,
    strip_model_recommendation_section,
)
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)

class LinkingAnalysisMessageGenerator:
    def __init__(self) -> None:
        self._llm, self._disabled_reason = self._create_client()

    async def generate(self, context: AnalysisMessageContext) -> GeneratedAnalysisMessage:
        static_message = build_static_message(context)
        if static_message is not None:
            return GeneratedAnalysisMessage(text=static_message, source="template")

        if self._llm is None:
            if self._disabled_reason:
                logger.warning(
                    "GigaChat отключен, будет использовано резервное сообщение: %s",
                    self._disabled_reason,
                )
            return GeneratedAnalysisMessage(
                text=build_fallback_message(context),
                source="fallback",
                error=self._disabled_reason,
            )

        prompt = self._build_prompt(context)
        try:
            response = await self._llm.ainvoke(prompt)
        except Exception as exc:  # pragma: no cover - зависит от внешнего API
            logger.exception("Не удалось сгенерировать сообщение анализа через GigaChat.")
            return GeneratedAnalysisMessage(
                text=build_fallback_message(context),
                source="fallback",
                error=str(exc),
            )

        message = self._extract_text(response.content).strip()
        if not message:
            return GeneratedAnalysisMessage(
                text=build_fallback_message(context),
                source="fallback",
                error="GigaChat вернул пустой ответ.",
            )

        return GeneratedAnalysisMessage(
            text=finalize_message(message, context),
            source="llm",
        )

    @staticmethod
    def _create_client() -> tuple[Any, str | None]:
        return create_gigachat_client()

    @staticmethod
    def _build_prompt(context: AnalysisMessageContext) -> str:
        payload = context.model_dump(mode="json")
        prompt = LinkingAnalysisMessageGenerator._resolve_prompt(context)
        if context.target_title:
            prompt += (
                "\nЕсли упоминаешь заголовок целевой страницы, используй его без изменений: "
                f"{json.dumps(context.target_title, ensure_ascii=False)}."
            )
        else:
            prompt += (
                "\nЕсли target_title пустой, не придумывай заголовок страницы. "
                "Называй ее только целевой страницей."
            )

        if context.placement_recommendations:
            prompt += (
                "\nplacement_recommendations это внутренние подсказки."
                " Не перечисляй страницы-кандидаты, не упоминай их заголовки и не выводи URL."
                " Система сама добавит несколько проверенных URL."
                " Сфокусируйся только на проблеме, влиянии на SEO и причине мягкой семантической оценки."
            )
        else:
            prompt += (
                "\nЕсли placement_recommendations пустой, не придумывай страницы и URL."
                " Дай только общую рекомендацию по улучшению внутренней перелинковки."
            )

        if context.budget_exhausted or context.level_truncated:
            prompt += (
                "\nЕсли budget_exhausted=true или level_truncated=true, не пиши, что плохая перелинковка уже доказана."
                " Формулируй аккуратно: текущий обход не подтвердил короткий путь, но обход был неполным."
                " Не утверждай, что shortest path гарантированно отсутствует."
                " Не перечисляй внутренние технические детали вроде budget, level_truncated, truncated_nodes или количества URL вне очереди."
            )

        return f"{prompt}\n\nДанные анализа:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

    @staticmethod
    def _resolve_prompt(context: AnalysisMessageContext) -> str:
        status = OptimizationStatus(context.optimization_status)
        if status == OptimizationStatus.GOOD:
            return settings.gigachat_good_message_prompt
        return settings.gigachat_bad_message_prompt

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return " ".join(part.strip() for part in parts if part.strip())
        return ""

    _build_static_message = staticmethod(build_static_message)
    _build_fallback_message = staticmethod(build_fallback_message)
    _finalize_message = staticmethod(finalize_message)
    _strip_model_recommendation_section = staticmethod(strip_model_recommendation_section)
    _problem_intro = staticmethod(problem_intro)
    _should_render_multiple_candidates = staticmethod(should_render_multiple_candidates)
    _build_soft_candidates_message = staticmethod(build_soft_candidates_message)
    _append_soft_candidates_message = staticmethod(append_soft_candidates_message)
    _soft_candidates_sentence = staticmethod(soft_candidates_sentence)
    _soft_candidate_label = staticmethod(soft_candidate_label)
    _has_site_access_issue = staticmethod(has_site_access_issue)
    _normalize_message = staticmethod(normalize_message)
