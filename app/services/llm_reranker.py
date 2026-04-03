from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.gigachat_client import create_gigachat_client
from app.services.link_placement import PlacementRecommendation
from app.services.matcher import SearchTarget
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class PlacementRecommendationReranker:
    def __init__(self) -> None:
        self._llm, self._disabled_reason = self._create_client()

    def _create_client(self) -> tuple[Any, str | None]:
        if not settings.gigachat_rerank_enabled:
            return None, "GigaChat-ранкер отключен в конфигурации."
        return create_gigachat_client(temperature=settings.gigachat_rerank_temperature)

    async def rerank(
        self,
        *,
        target: SearchTarget,
        recommendations: list[PlacementRecommendation],
    ) -> list[PlacementRecommendation]:
        if self._llm is None or len(recommendations) < 2:
            return recommendations

        candidate_limit = max(2, settings.gigachat_rerank_max_candidates)
        candidates = recommendations[:candidate_limit]
        prompt = self._build_prompt(target=target, recommendations=candidates)

        try:
            response = await self._llm.ainvoke(prompt)
        except Exception as exc:  # pragma: no cover - depends on external API
            logger.warning("Не удалось пересортировать рекомендации через GigaChat: %s", exc)
            return recommendations

        selected_index = self._parse_selected_index(self._extract_text(response.content), len(candidates))
        if selected_index is None or selected_index == 1:
            return recommendations

        selected = candidates[selected_index - 1]
        return [selected] + [recommendation for recommendation in recommendations if recommendation is not selected]

    @staticmethod
    def _build_prompt(
        *,
        target: SearchTarget,
        recommendations: list[PlacementRecommendation],
    ) -> str:
        payload = {
            "target": {
                "url": target.url,
                "title": target.title,
                "priority_terms": list(target.priority_terms),
                "signature_terms": list(target.signature_terms),
                "branch_terms": list(target.branch_terms),
            },
            "candidates": [
                {
                    "index": index,
                    "source_url": recommendation.source_url,
                    "source_title": recommendation.source_title,
                    "source_depth": recommendation.source_depth,
                    "projected_steps_to_target": recommendation.projected_steps_to_target,
                    "reason": recommendation.reason,
                }
                for index, recommendation in enumerate(recommendations, start=1)
            ],
        }
        instructions = (
            "Ты пересортировываешь кандидатов на страницу-донор для внутренней ссылки. "
            "Выбери ровно один лучший уже существующий вариант из переданного списка. "
            "На первом месте должна быть семантическая релевантность: тот же тематический кластер, "
            "тот же раздел, тот же регион, сущность, продукт или инициатива, а также то же пользовательское намерение. "
            "Предпочитай страницу той же инициативы или подраздела, а не страницу, которая совпадает только по широкому региону. "
            "Глубину перехода учитывай только как вторичный сигнал. "
            "Штрафуй общие новостные страницы, широкие хабы и страницы, совпадающие только по одному-двум общим ключам. "
            'Верни только строгий JSON: {"selected_index": <целое число 1..N>, "why": "<короткая причина>"}. '
            "Не придумывай URL. Не используй markdown."
        )
        return f"{instructions}\n\nДанные:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

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

    @staticmethod
    def _parse_selected_index(message: str, candidate_count: int) -> int | None:
        if not message:
            return None

        payload: dict[str, Any] | None = None
        try:
            parsed = json.loads(message)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", message, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    payload = None

        if payload is None:
            return None

        selected_index = payload.get("selected_index")
        if not isinstance(selected_index, int):
            return None
        if selected_index < 1 or selected_index > candidate_count:
            return None
        return selected_index
