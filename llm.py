from __future__ import annotations

import json
import re
from typing import Any

from config import (
    GOOD_DEPTH_THRESHOLD,
    LLM_FINAL_CANDIDATE_LIMIT,
    YANDEX_GPT_MAX_RETRIES,
    YANDEX_GPT_MAX_TOKENS,
    YANDEX_GPT_RERANK_MAX_CANDIDATES,
    YANDEX_GPT_RERANK_TEMPERATURE,
    YANDEX_GPT_TEMPERATURE,
    read_llm_secrets,
)
from models import AnalysisResult, Candidate
from prompts import FINAL_MESSAGE_PROMPT, RERANK_PROMPT, fallback_message

try:
    from langchain_community.chat_models import ChatYandexGPT
except ImportError:  # pragma: no cover
    ChatYandexGPT = None


async def rerank_candidates(*, target_url: str, candidates: list[Candidate]) -> tuple[list[Candidate], str, str]:
    candidates = candidates[:YANDEX_GPT_RERANK_MAX_CANDIDATES]
    if len(candidates) < 2:
        return candidates[:LLM_FINAL_CANDIDATE_LIMIT], "not-needed", ""
    client, disabled_reason = create_client(temperature=YANDEX_GPT_RERANK_TEMPERATURE, max_tokens=500)
    if client is None:
        return candidates[:LLM_FINAL_CANDIDATE_LIMIT], f"fallback: {disabled_reason}", ""

    payload = {
        "target": {"url": target_url},
        "candidates": [
            {
                "index": index,
                "url": candidate.url,
                "title": candidate.title,
                "h1": candidate.h1,
                "parent_section": candidate.parent_section,
                "depth": candidate.depth,
                "score": candidate.score,
                "source": candidate.source,
                "confidence": candidate.confidence,
                "reason": candidate.reason,
            }
            for index, candidate in enumerate(candidates, start=1)
        ],
    }
    try:
        response = await client.ainvoke(f"{RERANK_PROMPT}\n\nДанные:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
    except Exception as exc:  # pragma: no cover - external API
        return candidates[:LLM_FINAL_CANDIDATE_LIMIT], f"fallback: {type(exc).__name__}: {exc}", ""

    response_text = extract_text(response.content)
    indexes, explanation = parse_rerank_response(response_text, len(candidates))
    if not indexes:
        return candidates[:LLM_FINAL_CANDIDATE_LIMIT], "fallback: LLM returned no valid order", ""
    ordered = [candidates[index - 1] for index in indexes]
    ordered.extend(candidate for candidate in candidates if candidate not in ordered)
    return ordered[:LLM_FINAL_CANDIDATE_LIMIT], "llm", explanation


async def build_final_message(result: AnalysisResult) -> tuple[str, str]:
    client, disabled_reason = create_client(temperature=YANDEX_GPT_TEMPERATURE, max_tokens=YANDEX_GPT_MAX_TOKENS)
    if client is None:
        return (
            fallback_message(
                poor_linking=result.poor_linking,
                steps_to_target=result.steps_to_target,
                good_depth=GOOD_DEPTH_THRESHOLD,
                has_candidates=bool(result.candidates),
            ),
            f"fallback: {disabled_reason}",
        )
    payload = result.to_dict()
    payload.pop("message", None)
    payload.pop("target_url", None)
    for key in ("local_top5", "llm_top3", "candidates"):
        if isinstance(payload.get(key), list):
            payload[key] = [strip_llm_irrelevant_candidate_fields(candidate) for candidate in payload[key]]
    payload["target"] = {
        "path": path_without_slug(result.target_url),
        "linking_status": result.linking_status,
        "poor_linking": result.poor_linking,
        "steps_to_target": result.steps_to_target,
        "status": result.target_status,
        "error": result.target_error,
    }
    try:
        response = await client.ainvoke(f"{FINAL_MESSAGE_PROMPT}\n\nДанные анализа:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
    except Exception as exc:  # pragma: no cover - external API
        return (
            fallback_message(
                poor_linking=result.poor_linking,
                steps_to_target=result.steps_to_target,
                good_depth=GOOD_DEPTH_THRESHOLD,
                has_candidates=bool(result.candidates),
            ),
            f"fallback: {type(exc).__name__}: {exc}",
        )
    text = normalize_message(extract_text(response.content))
    if not text:
        return (
            fallback_message(
                poor_linking=result.poor_linking,
                steps_to_target=result.steps_to_target,
                good_depth=GOOD_DEPTH_THRESHOLD,
                has_candidates=bool(result.candidates),
            ),
            "fallback: empty LLM response",
        )
    return text, "llm"


def strip_llm_irrelevant_candidate_fields(candidate: Any) -> Any:
    if not isinstance(candidate, dict):
        return candidate
    source = candidate.get("source")
    type_hint_by_source = {
        "parsed": "parsed topical page",
        "parsed_section": "parsed page from the same thematic branch",
        "lexical_reserve": "site-wide lexical reserve candidate selected by topic words in url or headings",
        "section_url": "branch-near url from sitemap or navigation without confirmed html",
        "section_hub": "thematic hub page from the same branch",
        "best_effort": "weak fallback candidate",
    }
    return {
        "url": candidate.get("url", ""),
        "title": candidate.get("title", ""),
        "h1": candidate.get("h1", ""),
        "parent_section": candidate.get("parent_section", ""),
        "depth": candidate.get("depth"),
        "source": source,
        "confidence": candidate.get("confidence", ""),
        "reason": candidate.get("reason", ""),
        "type_hint": type_hint_by_source.get(source, "candidate"),
    }


def create_client(*, temperature: float, max_tokens: int) -> tuple[Any, str | None]:
    if ChatYandexGPT is None:
        return None, "langchain-community is not installed"
    secrets = read_llm_secrets()
    if not secrets.configured:
        return None, "YandexGPT secrets are not configured"
    kwargs = {
        "api_key": secrets.api_key,
        "folder_id": secrets.folder_id,
        "model_uri": secrets.model_uri,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_retries": YANDEX_GPT_MAX_RETRIES,
    }
    try:
        return ChatYandexGPT(**{key: value for key, value in kwargs.items() if value is not None}), None
    except Exception as exc:  # pragma: no cover - external client setup
        return None, f"failed to initialize YandexGPT: {exc}"


def parse_rerank_response(text: str, candidate_count: int) -> tuple[list[int], str]:
    payload: dict[str, Any] | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = None
    if not payload:
        indexes = parse_loose_indexes(text, candidate_count)
        return indexes, ""
    raw_indexes = payload.get("ordered_indexes")
    if not isinstance(raw_indexes, list):
        selected = payload.get("selected_index")
        raw_indexes = [selected] if isinstance(selected, int) else []
    indexes: list[int] = []
    for value in raw_indexes:
        parsed_value = int(value) if isinstance(value, str) and value.isdigit() else value
        if isinstance(parsed_value, int) and 1 <= parsed_value <= candidate_count and parsed_value not in indexes:
            indexes.append(parsed_value)
    explanation = payload.get("why") or payload.get("explanation") or ""
    return indexes, str(explanation).strip()


def parse_loose_indexes(text: str, candidate_count: int) -> list[int]:
    indexes: list[int] = []
    for raw_value in re.findall(r"\b\d+\b", text):
        value = int(raw_value)
        if 1 <= value <= candidate_count and value not in indexes:
            indexes.append(value)
    return indexes


def parse_ordered_indexes(text: str, candidate_count: int) -> list[int]:
    indexes, _explanation = parse_rerank_response(text, candidate_count)
    return indexes


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(parts)
    return ""


def normalize_message(message: str) -> str:
    normalized = re.sub(r"\s+", " ", message).strip()
    return normalized.strip(" \"'")


def path_without_slug(url: str) -> str:
    match = re.match(r"https?://[^/]+(?P<path>/.*)?", url)
    path = match.group("path") if match else ""
    if not path:
        return "/"
    parts = [part for part in path.split("/") if part]
    if len(parts) <= 1:
        return "/"
    return "/" + "/".join(parts[:-1])
