from __future__ import annotations

import re
from urllib.parse import urlsplit

from models import Candidate, Page

from .constants import BLOCKED_PAGE_MARKERS, GENERIC_HEADING_TOKENS, JUNK_PATH_PARTS, VERTICAL_GROUPS
from .profiles import TokenProfile, branch_token_profile, page_token_profile, tokenize
from .sections import (
    SectionContext,
    common_path_prefix_len,
    is_near_target_parent,
    same_url,
    section_tier,
    section_tier_bonus,
)


def score_candidate(
    *,
    candidate_url: str,
    page: Page | None,
    target_url: str,
    target_profile: TokenProfile,
    target_branch_profile: TokenProfile,
    section_context: SectionContext,
    depth: int | None,
) -> tuple[float, str]:
    candidate_profile = page_token_profile(page, candidate_url)
    candidate_branch_profile = branch_token_profile(candidate_url)
    topical_overlap = target_profile.topical & candidate_profile.topical
    model_overlap = target_profile.model_like & candidate_profile.model_like
    branch_overlap = target_branch_profile.strong & candidate_branch_profile.strong
    location_overlap = target_profile.location & candidate_profile.location
    generic_overlap = target_profile.generic & candidate_profile.generic
    common_branch = common_path_prefix_len(candidate_url, target_url)
    tier, tier_reason = section_tier(candidate_url, page, section_context, candidate_profile)

    heading_overlap = heading_overlap_tokens(page, target_profile)
    gate, evidence_label = candidate_gate(
        target_url=target_url,
        candidate_url=candidate_url,
        page=page,
        target_profile=target_profile,
        candidate_profile=candidate_profile,
        topical_overlap=topical_overlap,
        model_overlap=model_overlap,
        branch_overlap=branch_overlap,
        section_tier_value=tier,
        heading_overlap=heading_overlap,
    )
    if not gate:
        return 0.0, ""

    vertical_penalty = unrelated_vertical_penalty(target_profile, candidate_profile)
    general_penalty = general_page_penalty(candidate_url, target_url, page, candidate_profile)
    heading_bonus = heading_quality_score(page, candidate_profile)
    linkability_bonus = linkability_score(page, depth)
    branch_bonus = min(common_branch, 4) * 1.5 + len(branch_overlap) * 2.5
    section_bonus = section_tier_bonus(tier)
    topical_score = len(topical_overlap) * 10.0 + len(model_overlap) * 14.0 + len(heading_overlap) * 4.0
    location_score = min(len(location_overlap), 1) * 0.5
    generic_penalty = len(generic_overlap) * 2.0
    parsed_bonus = 2.0 if page is not None else 0.0
    penalties = url_penalty(candidate_url) + general_penalty + vertical_penalty + generic_penalty
    if is_near_target_parent(candidate_url, target_url) and tier > 2:
        penalties += 18.0
    if not (topical_overlap or model_overlap or heading_overlap):
        penalties += 10.0
    if tier >= 4:
        penalties += 4.0

    score = topical_score + branch_bonus + section_bonus + heading_bonus + linkability_bonus + location_score + parsed_bonus - penalties
    if score <= 0:
        return 0.0, ""
    return score, score_reason(
        tier=tier,
        tier_reason=tier_reason,
        topical_score=topical_score,
        branch_bonus=branch_bonus,
        section_bonus=section_bonus,
        heading_bonus=heading_bonus,
        linkability_bonus=linkability_bonus,
        penalties=penalties,
        evidence_label=evidence_label,
        topical_overlap=topical_overlap,
        model_overlap=model_overlap,
        branch_overlap=branch_overlap,
        heading_overlap=heading_overlap,
        location_overlap=location_overlap,
    )


def parsed_section_candidate_score(
    *,
    candidate_url: str,
    page: Page,
    section_context: SectionContext,
    profile: TokenProfile,
    tier: int,
    tier_reason: str,
) -> tuple[float, str]:
    candidate_terms = profile.strong | branch_token_profile(candidate_url).strong
    topic_overlap = candidate_terms & section_context.topic_terms
    series_overlap = candidate_terms & section_context.series_terms
    parent_overlap = candidate_terms & section_context.parent_terms
    structural_overlap = series_overlap | parent_overlap
    if not topic_overlap and not (tier <= 2 and len(structural_overlap) >= 2):
        return 0.0, ""
    section_score = section_tier_bonus(tier)
    topic_score = len(topic_overlap) * 12.0
    series_score = len(structural_overlap) * 4.0
    heading_score = max(heading_quality_score(page, profile), 0.0)
    depth_penalty = float(page.depth or 0) * 0.5
    generic_penalty = len(profile.generic) * 2.0
    structural_penalty = 0.0 if topic_overlap else 12.0
    score = section_score + topic_score + series_score + heading_score - depth_penalty - generic_penalty - structural_penalty
    reason_parts = [
        f"parsed section candidate; tier={tier} {tier_reason}",
        f"topic={topic_score:.1f}",
        f"series={series_score:.1f}",
        f"section={section_score:.1f}",
        f"evidence={'topic' if topic_overlap else 'structural_fallback'}",
    ]
    if topic_overlap:
        reason_parts.append("topic: " + ", ".join(sorted(topic_overlap)[:8]))
    if series_overlap:
        reason_parts.append("series: " + ", ".join(sorted(series_overlap)[:8]))
    if parent_overlap:
        reason_parts.append("parent: " + ", ".join(sorted(parent_overlap)[:8]))
    return score, "; ".join(reason_parts)


def best_effort_score_candidate(
    *,
    candidate_url: str,
    page: Page,
    target_url: str,
    target_profile: TokenProfile,
    target_branch_profile: TokenProfile,
    section_context: SectionContext,
) -> tuple[float, str]:
    candidate_profile = page_token_profile(page, candidate_url)
    candidate_branch_profile = branch_token_profile(candidate_url)
    topical_overlap = target_profile.topical & candidate_profile.topical
    model_overlap = target_profile.model_like & candidate_profile.model_like
    branch_overlap = target_branch_profile.strong & candidate_branch_profile.strong
    location_overlap = target_profile.location & candidate_profile.location
    vertical_shared = share_vertical(target_profile, candidate_profile)
    tier, tier_reason = section_tier(candidate_url, page, section_context, candidate_profile)
    heading_overlap = heading_overlap_tokens(page, target_profile)
    if tier > 5:
        return 0.0, ""
    if not (topical_overlap or model_overlap or heading_overlap):
        if tier > 2:
            return 0.0, ""
        if len(branch_overlap) < 2:
            return 0.0, ""

    topical_score = len(topical_overlap) * 8.0 + len(model_overlap) * 11.0 + len(heading_overlap) * 3.0
    branch_score = len(branch_overlap) * 2.5 + min(common_path_prefix_len(candidate_url, target_url), 3) * 1.0
    section_score = section_tier_bonus(tier)
    vertical_score = 5.0 if vertical_shared else 0.0
    heading_score = max(heading_quality_score(page, candidate_profile), 0.0)
    location_score = 0.25 if location_overlap else 0.0
    depth_penalty = float(page.depth or 0) * 0.35
    penalties = (
        general_page_penalty(candidate_url, target_url, page, candidate_profile)
        + unrelated_vertical_penalty(target_profile, candidate_profile)
        + url_penalty(candidate_url)
        + depth_penalty
    )
    if not (topical_overlap or model_overlap or heading_overlap):
        penalties += 14.0
    score = topical_score + branch_score + section_score + vertical_score + heading_score + location_score - penalties
    parts = [
        "best_effort fallback",
        f"tier={tier} {tier_reason}",
        f"topical={topical_score:.1f}",
        f"branch={branch_score:.1f}",
        f"section={section_score:.1f}",
        f"vertical={vertical_score:.1f}",
        f"penalty={penalties:.1f}",
        f"evidence={'topic' if (topical_overlap or model_overlap or heading_overlap) else 'structural_fallback'}",
    ]
    if topical_overlap:
        parts.append("tokens: " + ", ".join(sorted(topical_overlap)[:8]))
    if model_overlap:
        parts.append("models: " + ", ".join(sorted(model_overlap)[:8]))
    if branch_overlap:
        parts.append("branch: " + ", ".join(sorted(branch_overlap)[:8]))
    if location_overlap:
        parts.append("location weak: " + ", ".join(sorted(location_overlap)[:4]))
    return score, "; ".join(parts)


def candidate_gate(
    *,
    target_url: str,
    candidate_url: str,
    page: Page | None,
    target_profile: TokenProfile,
    candidate_profile: TokenProfile,
    topical_overlap: set[str],
    model_overlap: set[str],
    branch_overlap: set[str],
    section_tier_value: int,
    heading_overlap: set[str],
) -> tuple[bool, str]:
    if page is None:
        return False, ""
    if same_url(candidate_url, target_url):
        return False, ""
    if is_general_shell_page(candidate_url, page, candidate_profile):
        return False, ""
    if unrelated_vertical_penalty(target_profile, candidate_profile) >= 20.0:
        return False, ""
    strong_topic_overlap = topical_overlap | model_overlap
    if len(strong_topic_overlap) >= 2:
        return True, "strong_topic"
    if strong_topic_overlap and heading_overlap:
        return True, "topic_supported"
    if strong_topic_overlap and section_tier_value <= 3:
        return True, "topic_with_section_support"
    if strong_topic_overlap and share_vertical(target_profile, candidate_profile):
        return True, "topic_with_vertical_support"
    if section_tier_value == 1 and (heading_overlap or len(branch_overlap) >= 2):
        return True, "sibling_supported"
    if section_tier_value == 2 and heading_overlap:
        return True, "hub_supported"
    if model_overlap and has_specific_heading_or_text(page, target_profile):
        return True, "model_supported"
    return False, ""


def candidate_sort_key(candidate: Candidate) -> tuple[float, int, str]:
    return (-candidate.score, candidate.depth if candidate.depth is not None else 999, candidate.url)


def score_reason(
    *,
    tier: int,
    tier_reason: str,
    topical_score: float,
    branch_bonus: float,
    section_bonus: float,
    heading_bonus: float,
    linkability_bonus: float,
    penalties: float,
    evidence_label: str,
    topical_overlap: set[str],
    model_overlap: set[str],
    branch_overlap: set[str],
    heading_overlap: set[str],
    location_overlap: set[str],
) -> str:
    parts = [
        f"tier={tier} {tier_reason}",
        f"evidence={evidence_label}",
        f"topical={topical_score:.1f}",
        f"branch={branch_bonus:.1f}",
        f"section={section_bonus:.1f}",
        f"heading={heading_bonus:.1f}",
        f"linkability={linkability_bonus:.1f}",
        f"penalty={penalties:.1f}",
    ]
    if topical_overlap:
        parts.append("tokens: " + ", ".join(sorted(topical_overlap)[:8]))
    if model_overlap:
        parts.append("models: " + ", ".join(sorted(model_overlap)[:8]))
    if branch_overlap:
        parts.append("branch: " + ", ".join(sorted(branch_overlap)[:8]))
    if heading_overlap:
        parts.append("heading: " + ", ".join(sorted(heading_overlap)[:8]))
    if location_overlap:
        parts.append("location weak: " + ", ".join(sorted(location_overlap)[:4]))
    return "; ".join(parts)


def url_penalty(url: str) -> float:
    parsed = urlsplit(url)
    penalty = 0.0
    if parsed.query:
        penalty += 2.0
    parts = {part.casefold() for part in parsed.path.split("/") if part}
    if parts & JUNK_PATH_PARTS:
        penalty += 8.0
    return penalty


def general_page_penalty(candidate_url: str, target_url: str, page: Page | None, profile: TokenProfile) -> float:
    parts = [part for part in urlsplit(candidate_url).path.split("/") if part]
    penalty = 0.0
    if len(parts) <= 1:
        penalty += 18.0
    if len(parts) == 1 and (profile.location or not profile.strong):
        penalty += 25.0
    if profile.generic and not profile.strong:
        penalty += 20.0
    if page is not None and is_generic_heading(page.title):
        penalty += 8.0
    if page is not None and is_generic_heading(page.h1):
        penalty += 8.0
    if common_path_prefix_len(candidate_url, target_url) <= 1 and len(profile.strong) < 2:
        penalty += 12.0
    return penalty


def is_blocked_page(page: Page | None) -> bool:
    if page is None:
        return False
    haystack = " ".join(part for part in (page.title, page.h1, page.text) if part).casefold()
    if not haystack:
        return False
    return any(marker in haystack for marker in BLOCKED_PAGE_MARKERS)


def is_general_shell_page(url: str, page: Page | None, profile: TokenProfile) -> bool:
    if is_blocked_page(page):
        return True
    parts = [part for part in urlsplit(url).path.split("/") if part]
    if len(parts) == 0:
        return True
    if len(parts) == 1 and (profile.location or not profile.strong):
        return True
    if page is not None and is_generic_heading(page.title) and is_generic_heading(page.h1):
        return True
    if not profile.strong and (profile.generic or profile.location or profile.weak):
        return True
    return False


def is_generic_heading(value: str) -> bool:
    if not value:
        return False
    tokens = set(tokenize(value))
    if not tokens:
        return True
    generic_count = len(tokens & GENERIC_HEADING_TOKENS)
    strong_count = len(tokens - GENERIC_HEADING_TOKENS)
    if strong_count == 0 and generic_count > 0:
        return True
    if generic_count >= strong_count + 2:
        return True
    return False


def heading_quality_score(page: Page | None, profile: TokenProfile) -> float:
    if page is None:
        return 0.0
    score = 0.0
    if meaningful_heading(page.title):
        score += 1.5
    if meaningful_heading(page.h1):
        score += 1.5
    if is_generic_heading(page.title):
        score -= 2.0
    if is_generic_heading(page.h1):
        score -= 2.0
    if len(profile.model_like) >= 1:
        score += 1.0
    return score


def meaningful_heading(value: str) -> bool:
    if is_generic_heading(value):
        return False
    tokens = tokenize(value)
    return 2 <= len(tokens) <= 14


def has_specific_heading_or_text(page: Page, target_profile: TokenProfile) -> bool:
    page_profile = page_token_profile(page, page.url)
    return bool(page_profile.strong & target_profile.strong)


def heading_overlap_tokens(page: Page | None, target_profile: TokenProfile) -> set[str]:
    if page is None:
        return set()
    tokens: set[str] = set()
    for value in (page.title, page.h1, " ".join(page.breadcrumbs)):
        if not value:
            continue
        tokens.update(tokenize(value))
    return tokens & target_profile.strong


def linkability_score(page: Page | None, depth: int | None) -> float:
    score = 0.0
    if depth is not None:
        if depth <= 2:
            score += 1.0
        elif depth <= 4:
            score += 0.5
    if page is not None:
        if page.links:
            score += 0.5
        if page.internal_link_count > 80:
            score -= 1.0
    return score


def unrelated_vertical_penalty(target_profile: TokenProfile, candidate_profile: TokenProfile) -> float:
    target_verticals = detect_verticals(target_profile.strong)
    candidate_verticals = detect_verticals(candidate_profile.strong)
    if not target_verticals or not candidate_verticals:
        return 0.0
    if target_verticals & candidate_verticals:
        return 0.0
    return 25.0


def share_vertical(target_profile: TokenProfile, candidate_profile: TokenProfile) -> bool:
    target_verticals = detect_verticals(target_profile.strong)
    candidate_verticals = detect_verticals(candidate_profile.strong)
    return bool(target_verticals and candidate_verticals and target_verticals & candidate_verticals)


def detect_verticals(tokens: set[str]) -> set[str]:
    verticals = set()
    for name, markers in VERTICAL_GROUPS.items():
        if tokens & markers:
            verticals.add(name)
    return verticals
