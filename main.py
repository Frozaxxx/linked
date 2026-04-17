from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.main import app
from app.services.fetcher import AsyncFetcher
from app.services.parser import ExtractionRule, extract_fields
from app.settings import get_settings


__all__ = ["app"]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch and extract data from a page.")
    parser.add_argument("--url", help="URL to fetch.")
    parser.add_argument("--selector", action="append", help="CSS selector to extract. Can be passed multiple times.")
    parser.add_argument("--attr", help="Attribute to extract for all CLI selectors, for example href.")
    parser.add_argument("--config", help="Path to JSON config with url and rules.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser


def _rules_from_config(config: dict[str, Any]) -> tuple[str, list[ExtractionRule]]:
    url = str(config["url"])
    raw_rules = config.get("rules") or []
    rules = [ExtractionRule(**rule) for rule in raw_rules]
    return url, rules


def _rules_from_args(args: argparse.Namespace) -> tuple[str, list[ExtractionRule]]:
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        return _rules_from_config(config)
    if not args.url or not args.selector:
        raise SystemExit("--url and at least one --selector are required when --config is not used")
    rules = [
        ExtractionRule(
            name=f"selector_{index}",
            selector=selector,
            attr=args.attr,
            multiple=True,
        )
        for index, selector in enumerate(args.selector, start=1)
    ]
    return args.url, rules


async def _run_cli() -> int:
    args = _build_arg_parser().parse_args()
    url, rules = _rules_from_args(args)
    settings = get_settings()
    fetcher = AsyncFetcher(timeout_seconds=settings.request_timeout_seconds, retry_count=settings.request_retry_count)
    async with fetcher.create_client() as session:
        document = await fetcher.fetch(session, url)
    if document is None:
        print(json.dumps({"ok": False, "url": url, "error": "fetch failed"}, ensure_ascii=False))
        return 2
    result = extract_fields(document.body, requested_url=url, final_url=document.final_url, rules=rules)
    print(result.model_dump_json(indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_cli()))
