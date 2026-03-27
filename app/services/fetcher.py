from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.settings import get_settings


settings = get_settings()


DEFAULT_HEADERS = {
    "User-Agent": settings.fetch_user_agent,
    "Accept": settings.fetch_accept_header,
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
        for attempt in range(self._retry_count + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return FetchedDocument(
                    requested_url=url,
                    final_url=str(response.url),
                    body=response.text,
                    content_type=response.headers.get("content-type", ""),
                )
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError):
                if attempt >= self._retry_count:
                    return None
                await asyncio.sleep(0.3 * (attempt + 1))

        return None
