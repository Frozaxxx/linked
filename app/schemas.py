from __future__ import annotations

from enum import Enum

from pydantic import AnyHttpUrl, BaseModel, Field

from app.settings import get_settings

try:
    from pydantic import model_validator

    PYDANTIC_V2 = True
except ImportError:  # pragma: no cover
    from pydantic import root_validator

    PYDANTIC_V2 = False


settings = get_settings()


class OptimizationStatus(str, Enum):
    GOOD = "good"
    BAD = "bad"
    NOT_FOUND = "not_found"


class LinkingAnalyzeRequest(BaseModel):
    start_url: AnyHttpUrl = Field(
        ...,
        description="Site page where the crawl starts.",
    )
    target_url: AnyHttpUrl | None = Field(
        default=None,
        description="Target page URL. Best option when you want exact depth to a known page.",
    )
    target_title: str | None = Field(
        default=None,
        min_length=1,
        description="Optional page lookup by title.",
    )
    target_text: str | None = Field(
        default=None,
        min_length=1,
        description="Optional page lookup by body text.",
    )
    timeout_seconds: float = Field(
        default=settings.request_timeout_seconds,
        gt=0,
        le=60,
        description="Timeout for a single HTTP request.",
    )
    retry_count: int = Field(
        default=settings.request_retry_count,
        ge=0,
        le=5,
        description="Retry count for transient network failures.",
    )

    if PYDANTIC_V2:
        model_config = {
            "json_schema_extra": {
                "example": {
                    "start_url": "https://example.com/",
                    "target_url": "https://example.com/catalog/target-page",
                    "target_title": None,
                    "target_text": None,
                    "timeout_seconds": settings.request_timeout_seconds,
                    "retry_count": settings.request_retry_count,
                }
            }
        }

    else:  # pragma: no cover

        class Config:
            schema_extra = {
                "example": {
                    "start_url": "https://example.com/",
                    "target_url": "https://example.com/catalog/target-page",
                    "target_title": None,
                    "target_text": None,
                    "timeout_seconds": settings.request_timeout_seconds,
                    "retry_count": settings.request_retry_count,
                }
            }

    if PYDANTIC_V2:

        @model_validator(mode="after")
        def validate_target(self) -> "LinkingAnalyzeRequest":
            if not any((self.target_url, self.target_title, self.target_text)):
                raise ValueError(
                    "At least one search criterion is required: target_url, target_title, or target_text."
                )
            return self

    else:  # pragma: no cover

        @root_validator
        def validate_target(cls, values: dict) -> dict:
            if not any((values.get("target_url"), values.get("target_title"), values.get("target_text"))):
                raise ValueError(
                    "At least one search criterion is required: target_url, target_title, or target_text."
                )
            return values


class AnalyzeTimings(BaseModel):
    total_ms: float
    match_ms: float | None
    sitemap_elapsed_ms: float | None
    sitemap_completed: bool


class LinkingAnalyzeResponse(BaseModel):
    start_url: str
    target_url: str | None
    found: bool
    matched_by: list[str]
    steps_to_target: int | None
    path: list[str]
    optimization_status: OptimizationStatus
    message: str
    pages_fetched: int
    pages_discovered: int
    sitemap_checked: bool
    found_in_sitemap: bool
    strategy: str
    timings: AnalyzeTimings

    if PYDANTIC_V2:
        model_config = {
            "json_schema_extra": {
                "example": {
                    "start_url": "https://example.com/",
                    "target_url": "https://example.com/catalog/target-page",
                    "found": True,
                    "matched_by": ["url"],
                    "steps_to_target": 3,
                    "path": [
                        "https://example.com/",
                        "https://example.com/catalog",
                        "https://example.com/catalog/widgets",
                        "https://example.com/catalog/target-page",
                    ],
                    "optimization_status": "good",
                    "message": "Хорошая перелинковка: целевая страница найдена за 3 шаг(а/ов), порог 4.",
                    "pages_fetched": 7,
                    "pages_discovered": 12,
                    "sitemap_checked": True,
                    "found_in_sitemap": True,
                    "strategy": "bfs + live sitemap",
                    "timings": {
                        "total_ms": 184.231,
                        "match_ms": 184.231,
                        "sitemap_elapsed_ms": 61.418,
                        "sitemap_completed": False,
                    },
                }
            }
        }

    else:  # pragma: no cover

        class Config:
            schema_extra = {
                "example": {
                    "start_url": "https://example.com/",
                    "target_url": "https://example.com/catalog/target-page",
                    "found": True,
                    "matched_by": ["url"],
                    "steps_to_target": 3,
                    "path": [
                        "https://example.com/",
                        "https://example.com/catalog",
                        "https://example.com/catalog/widgets",
                        "https://example.com/catalog/target-page",
                    ],
                    "optimization_status": "good",
                    "message": "Хорошая перелинковка: целевая страница найдена за 3 шаг(а/ов), порог 4.",
                    "pages_fetched": 7,
                    "pages_discovered": 12,
                    "sitemap_checked": True,
                    "found_in_sitemap": True,
                    "strategy": "bfs + live sitemap",
                    "timings": {
                        "total_ms": 184.231,
                        "match_ms": 184.231,
                        "sitemap_elapsed_ms": 61.418,
                        "sitemap_completed": False,
                    },
                }
            }
