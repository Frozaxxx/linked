from __future__ import annotations

import argparse
import asyncio
import json
import sys

import uvicorn

from api import app
from pipeline import analyze


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SEO Linked app or analyze a URL from CLI.")
    parser.add_argument("target_url", nargs="?", help="Target page URL. If omitted, starts the web app.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--host", default="127.0.0.1", help="Web app host.")
    parser.add_argument("--port", type=int, default=8000, help="Web app port.")
    return parser


async def run() -> int:
    args = build_parser().parse_args()
    if not args.target_url:
        run_server(host=args.host, port=args.port)
        return 0

    result = await analyze(args.target_url)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def run_server(*, host: str = "127.0.0.1", port: int = 8000) -> None:
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(asyncio.run(run()))
