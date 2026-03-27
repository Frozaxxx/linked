from __future__ import annotations

from fastapi import FastAPI

from app.schemas import LinkingAnalyzeRequest, LinkingAnalyzeResponse
from app.services.internal_linking import InternalLinkingAnalyzer
from app.settings import get_settings


settings = get_settings()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
)


@app.get("/health", tags=["system"])
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/internal-linking/analyze",
    response_model=LinkingAnalyzeResponse,
    tags=["internal-linking"],
    summary=settings.analyze_summary,
)
async def analyze_internal_linking(payload: LinkingAnalyzeRequest) -> LinkingAnalyzeResponse:
    analyzer = InternalLinkingAnalyzer(payload)
    return await analyzer.analyze()
