from __future__ import annotations

import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from app.schemas import LinkingAnalyzeRequest, LinkingAnalyzeResponse
from app.services.internal_linking import InternalLinkingAnalyzer
from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
exception_handler = logging.FileHandler(LOG_DIR / "internal-linking-errors.log", encoding="utf-8")
exception_handler.setLevel(logging.ERROR)
exception_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
if not any(
    isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == exception_handler.baseFilename
    for handler in logger.handlers
):
    logger.addHandler(exception_handler)
logger.setLevel(logging.ERROR)


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
        logger.exception("Unhandled error while analyzing internal linking.")
        raise

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
