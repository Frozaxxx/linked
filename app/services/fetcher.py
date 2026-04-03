from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


DEFAULT_HEADERS = {
    "User-Agent": settings.fetch_user_agent,
    "Accept": settings.fetch_accept_header,
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


@dataclass(slots=True)
class FetchedDocument:
    requested_url: str
    final_url: str
    body: str
    content_type: str


class AsyncFetcher:
    def __init__(
        self,
        timeout_seconds: float,
        retry_count: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._retry_count = retry_count
        self._transport = transport

    def create_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
            headers=DEFAULT_HEADERS,
        )

    async def fetch(self, client: httpx.AsyncClient, url: str) -> FetchedDocument | None:
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self._retry_count + 1),
            wait=wait_exponential(multiplier=0.3, min=0.3, max=2),
            retry=retry_if_exception(self._is_retryable_exception),
            reraise=True,
        )

        try:
            async for attempt in retryer:
                with attempt:
                    response = await client.get(url)
                    response.raise_for_status()
                    return FetchedDocument(
                        requested_url=url,
                        final_url=str(response.url),
                        body=response.text,
                        content_type=response.headers.get("content-type", ""),
                    )
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.debug("Failed to fetch %s after retries: %s", url, exc)
            return None

        return None

    @staticmethod
    def _is_retryable_exception(exc: BaseException) -> bool:
        if isinstance(exc, httpx.TimeoutException):
            return True

        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            return status_code in {408, 425, 429, 500, 502, 503, 504}

        if isinstance(exc, httpx.RequestError):
            return True

        return False
