from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.services.parser.models import ParsedRobotsTxt
from app.services.parser.urls import is_internal_url, normalize_url


ROBOTS_LINE_RE = re.compile(r"^\s*([^:#\s][^:]*)\s*:\s*(.*?)\s*$")


def parse_robots_txt(xml_body: str, base_url: str, allowed_host: str, user_agent: str) -> ParsedRobotsTxt:
    groups: list[dict[str, list[str]]] = []
    current_group = {"user_agents": [], "allow": [], "disallow": []}
    sitemaps: list[str] = []

    def flush_group() -> None:
        if current_group["user_agents"] or current_group["allow"] or current_group["disallow"]:
            groups.append(
                {
                    "user_agents": list(current_group["user_agents"]),
                    "allow": list(current_group["allow"]),
                    "disallow": list(current_group["disallow"]),
                }
            )
            current_group["user_agents"].clear()
            current_group["allow"].clear()
            current_group["disallow"].clear()

    for raw_line in xml_body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            flush_group()
            continue

        match = ROBOTS_LINE_RE.match(line)
        if match is None:
            continue

        field_name = match.group(1).strip().casefold()
        value = match.group(2).strip()
        if field_name == "user-agent":
            if current_group["allow"] or current_group["disallow"]:
                flush_group()
            if value:
                current_group["user_agents"].append(value.casefold())
            continue

        if field_name == "allow":
            if current_group["user_agents"] and value:
                current_group["allow"].append(normalize_robots_rule(value))
            continue

        if field_name == "disallow":
            if current_group["user_agents"] and value:
                current_group["disallow"].append(normalize_robots_rule(value))
            continue

        if field_name == "sitemap" and value:
            normalized = normalize_url(value, base_url, allow_ignored_extensions=True)
            if normalized and is_internal_url(normalized, allowed_host) and normalized not in sitemaps:
                sitemaps.append(normalized)

    flush_group()

    matched_groups = select_robots_groups(groups, user_agent=user_agent)
    allow_rules: list[str] = []
    disallow_rules: list[str] = []
    for group in matched_groups:
        allow_rules.extend(group["allow"])
        disallow_rules.extend(group["disallow"])

    return ParsedRobotsTxt(
        allow_rules=tuple(allow_rules),
        disallow_rules=tuple(disallow_rules),
        sitemap_urls=sitemaps,
    )


def select_robots_groups(groups: list[dict[str, list[str]]], *, user_agent: str) -> list[dict[str, list[str]]]:
    normalized_user_agent = user_agent.strip().casefold()
    exact_matches: list[dict[str, list[str]]] = []
    wildcard_matches: list[dict[str, list[str]]] = []

    for group in groups:
        user_agents = group["user_agents"]
        if any(agent != "*" and agent and agent in normalized_user_agent for agent in user_agents):
            exact_matches.append(group)
            continue
        if "*" in user_agents:
            wildcard_matches.append(group)

    if exact_matches:
        return exact_matches
    return wildcard_matches


def normalize_robots_rule(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme or parsed.netloc:
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    return value.strip() or "/"


def robots_url_path(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def best_robots_match_length(path: str, rules: tuple[str, ...]) -> int:
    best_length = -1
    for rule in rules:
        if robots_rule_matches(path, rule):
            best_length = max(best_length, len(rule))
    return best_length


def robots_rule_matches(path: str, rule: str) -> bool:
    if not rule:
        return False

    anchored = rule.endswith("$")
    escaped_rule = re.escape(rule[:-1] if anchored else rule)
    pattern = escaped_rule.replace(r"\*", ".*")
    if anchored:
        return re.match(rf"^{pattern}$", path) is not None
    return re.match(rf"^{pattern}", path) is not None
