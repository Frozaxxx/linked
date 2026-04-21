from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent


# Plain runtime constants. They mirror the meaningful non-secret defaults from
# the old app/settings.py, but stay explicit and local to the simplified app.
REQUEST_TIMEOUT_SECONDS = 20.0
REQUEST_RETRY_COUNT = 3
CRAWL_CONCURRENCY = 4
CRAWL_MAX_DEPTH = 4
GOOD_DEPTH_THRESHOLD = 4
DONOR_MAX_DEPTH = GOOD_DEPTH_THRESHOLD
ANALYZE_TIME_BUDGET_SECONDS = 180.0
MAX_CRAWL_LEVEL_SIZE = 800
MAX_PAGES = 200
HTTP_TIMEOUT_SECONDS = REQUEST_TIMEOUT_SECONDS
PLAYWRIGHT_TIMEOUT_MS = 180_000
ROBOTS_USER_AGENT = "*"
OBEY_ROBOTS_TXT = True
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)
FETCH_ACCEPT_HEADER = "text/html,application/xhtml+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"
SITEMAP_TIME_BUDGET_SECONDS = 12.0
SITEMAP_REQUEST_TIMEOUT_SECONDS = 4.0
SITEMAP_MAX_FILES = 32
SITEMAP_MAX_URLS = 20_000
PLAYWRIGHT_NETWORK_IDLE_TIMEOUT_MS = 5_000
PLAYWRIGHT_POST_LOAD_WAIT_MS = 250
RENDERED_HTML_MIN_LENGTH = 120
RETRYABLE_TARGET_HTTP_STATUSES = {403, 429, 503}
PLAYWRIGHT_BROWSER_NAME = "chromium"
PLAYWRIGHT_HEADLESS = True
BROWSER_WS_ENDPOINT = "ws://localhost:3000"
DEFAULT_BROWSER_TOKEN = "seo-linked-dev-token"
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
YANDEX_GPT_TEMPERATURE = 0.2
YANDEX_GPT_MAX_TOKENS = 1000
YANDEX_GPT_MAX_RETRIES = 1
YANDEX_GPT_RERANK_TEMPERATURE = 0.0
LOCAL_CANDIDATE_LIMIT = 5
LLM_FINAL_CANDIDATE_LIMIT = 3
# Product rule: final user-facing recommendations should contain 3 items.
# If strict semantic parsing finds fewer items, allow at most one section hub from
# the target branch, never random high-authority or boilerplate pages.
FINAL_RECOMMENDATION_COUNT = 3
YANDEX_GPT_RERANK_MAX_CANDIDATES = LOCAL_CANDIDATE_LIMIT


class LLMSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: str | None
    folder_id: str | None
    model_uri: str | None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and (self.folder_id or self.model_uri))


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load only missing process env values from a local .env file."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not (
            key.startswith("YANDEX_")
            or key.startswith("BROWSER")
            or key.startswith("FETCH_BROWSER")
            or key == "CHROMIUM_WS_ENDPOINT"
        ):
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def read_llm_secrets() -> LLMSecrets:
    load_env_file()
    return LLMSecrets(
        api_key=_blank_to_none(os.getenv("YANDEX_CLOUD_API_KEY")),
        folder_id=_blank_to_none(os.getenv("YANDEX_CLOUD_FOLDER_ID")),
        model_uri=_blank_to_none(os.getenv("YANDEX_GPT_MODEL_URI")),
    )


def read_browser_ws_endpoint() -> str | None:
    load_env_file()
    endpoint = (
        _blank_to_none(os.getenv("CHROMIUM_WS_ENDPOINT"))
        or _blank_to_none(os.getenv("FETCH_BROWSER_WS_ENDPOINT"))
        or _blank_to_none(os.getenv("BROWSER_WS_ENDPOINT"))
        or BROWSER_WS_ENDPOINT
    )
    if endpoint is None:
        return None
    token = (
        _blank_to_none(os.getenv("BROWSERLESS_TOKEN"))
        or _blank_to_none(os.getenv("FETCH_BROWSER_TOKEN"))
        or _blank_to_none(os.getenv("BROWSER_TOKEN"))
        or DEFAULT_BROWSER_TOKEN
    )
    return with_browser_token(endpoint, token)


def browser_ws_endpoint_candidates() -> list[str]:
    endpoint = read_browser_ws_endpoint()
    if endpoint is None:
        return []

    candidates = [endpoint]
    base = browser_ws_base(endpoint)
    token = (
        _blank_to_none(os.getenv("BROWSERLESS_TOKEN"))
        or _blank_to_none(os.getenv("FETCH_BROWSER_TOKEN"))
        or _blank_to_none(os.getenv("BROWSER_TOKEN"))
        or DEFAULT_BROWSER_TOKEN
    )
    for path in ("", "/chromium/playwright", "/playwright/chromium", "/playwright"):
        candidate = with_browser_token(f"{base}{path}", token)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def browser_ws_base(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def with_browser_token(endpoint: str, token: str | None) -> str:
    if not token or "token=" in endpoint:
        return endpoint
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}token={token}"


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
