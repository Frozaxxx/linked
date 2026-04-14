from __future__ import annotations

import logging

from fastapi import APIRouter

from app.schemas import LinkingAnalyzeRequest, LinkingAnalyzeResponse
from app.services.internal_linking import InternalLinkingAnalyzer
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter(tags=["РїРµСЂРµР»РёРЅРєРѕРІРєР°"])


@router.post(
    "/api/v1/internal-linking/analyze",
    response_model=LinkingAnalyzeResponse,
    summary=settings.analyze_summary,
)
async def analyze_internal_linking(payload: LinkingAnalyzeRequest) -> LinkingAnalyzeResponse:
    analyzer = InternalLinkingAnalyzer(payload)
    try:
        return await analyzer.analyze()
    except Exception:
        logger.exception(
            "Unhandled error while analyzing internal linking: target_url=%s",
            payload.target_url,
        )
        raise
