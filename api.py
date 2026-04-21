from __future__ import annotations

import asyncio
import sys
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
from pydantic import BaseModel, HttpUrl

from crawler_core.browser import open_browser
from pipeline import analyze


class AnalyzeRequest(BaseModel):
    target_url: HttpUrl


T = TypeVar("T")
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Simplified internal linking analyzer",
        version="0.1.0",
        description="Упрощенная отдельная версия анализа внутренней перелинковки.",
    )

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def frontend_index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.post("/analyze")
    async def analyze_internal_linking(payload: AnalyzeRequest) -> dict:
        try:
            result = await run_in_playwright_loop(lambda: analyze(str(payload.target_url)))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            ) from exc
        return result.to_dict()

    @app.get("/health/browser")
    async def browser_health() -> dict:
        try:
            text = await run_in_playwright_loop(check_browser)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            ) from exc
        return {"ok": text == "ok", "browser": "connected"}

    return app


async def run_in_playwright_loop(coro_factory: Callable[[], Awaitable[T]]) -> T:
    return await asyncio.to_thread(run_coro_in_fresh_loop, coro_factory)


def run_coro_in_fresh_loop(coro_factory: Callable[[], Awaitable[T]]) -> T:
    if sys.platform == "win32":
        loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
    else:
        loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro_factory())
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


async def check_browser() -> str:
    async with async_playwright() as playwright:
        browser = await open_browser(playwright)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("data:text/html,<h1>ok</h1>")
            return await page.locator("h1").inner_text()
        finally:
            await browser.close()


app = create_app()
