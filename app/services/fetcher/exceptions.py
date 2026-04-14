from __future__ import annotations

from typing import Any


try:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover
    PlaywrightError = Any

    class PlaywrightTimeoutError(Exception):
        pass


class BrowserHTTPStatusError(Exception):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"Browser fetch failed with status {status_code} for {url}")


class BrowserNoDocumentResponseError(PlaywrightError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"No document response received for {url}")
