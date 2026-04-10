from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from app.logging_config import configure_logging
from app.schemas import LinkingAnalyzeRequest, LinkingAnalyzeResponse
from app.services.internal_linking import InternalLinkingAnalyzer
from app.settings import get_settings


settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
)


@app.get("/health", tags=["система"])
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/internal-linking/analyze",
    response_model=LinkingAnalyzeResponse,
    tags=["перелинковка"],
    summary=settings.analyze_summary,
)
async def analyze_internal_linking(payload: LinkingAnalyzeRequest) -> LinkingAnalyzeResponse:
    analyzer = InternalLinkingAnalyzer(payload)
    try:
        return await analyzer.analyze()
    except Exception:
        logger.exception(
            "Unhandled error while analyzing internal linking: start_url=%s target_url=%s",
            payload.start_url,
            payload.target_url,
        )
        raise

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
