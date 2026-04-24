from __future__ import annotations

import re
from urllib.parse import urlsplit

from config import DONOR_MAX_DEPTH, FINAL_RECOMMENDATION_COUNT, LOCAL_CANDIDATE_LIMIT
from models import Candidate, Page

from .constants import JUNK_PATH_PARTS
from .profiles import branch_token_profile, domain_tokens, page_token_profile, token_profile_from_text
from .scoring import (
    best_effort_score_candidate,
    candidate_sort_key,
    is_blocked_page,
    is_general_shell_page,
    parsed_section_candidate_score,
    score_candidate,
    unrelated_vertical_penalty,
    url_penalty,
)
from .sections import (
    SectionContext,
    build_section_context,
    common_path_prefix_len,
    estimated_structural_depth,
    is_direct_target_parent,
    is_parent_path,
    is_near_target_parent,
    parent_prefix_bonus,
    parent_section_from_url,
    path_parts,
    same_url,
    section_tier,
    section_tier_bonus,
    structural_hub_label,
    target_parent_urls,
)


def choose_top_candidates(
    *,
    target_url: str,
    target_page: Page | None,
    pages: dict[str, Page],
    discovered_urls: set[str],
    excluded_urls: set[str],
    limit: int = LOCAL_CANDIDATE_LIMIT,
) -> list[Candidate]:
    target_profile = page_token_profile(target_page, target_url)
    target_branch_profile = branch_token_profile(target_url)
    section_context = build_section_context(target_url, target_page)
    parsed_candidates: list[Candidate] = []
    alias_candidates: list[Candidate] = []
    known_depths = {url: page.depth for url, page in pages.items()}

    for url in discovered_urls | set(pages):
        page = pages.get(url)
        if should_skip_url(url, target_url, excluded_urls):
            continue
        if not is_reachable_donor(page):
            continue
        if page is not None and should_skip_page(page, target_url):
            continue

        score, reason = score_candidate(
            candidate_url=url,
            page=page,
            target_url=target_url,
            target_profile=target_profile,
            target_branch_profile=target_branch_profile,
            section_context=section_context,
            depth=known_depths.get(url),
        )
        if score <= 0:
            continue
        candidate = Candidate(
            url=url,
            title=page.title if page else "",
            h1=page.h1 if page else "",
            parent_section=page.parent_section if page else parent_section_from_url(url),
            depth=known_depths.get(url),
            internal_link_count=page.internal_link_count if page else 0,
            score=round(score, 3),
            reason=reason,
            source="parsed",
            confidence="high" if "evidence=strong_topic" in reason else "normal",
        )
        if is_short_alias_of_target_branch(url, target_url):
            alias_candidates.append(candidate)
        else:
            parsed_candidates.append(candidate)

    parsed_candidates.sort(key=candidate_sort_key)
    alias_candidates.sort(key=candidate_sort_key)
    candidates = (parsed_candidates + alias_candidates)[:limit]
    if len(candidates) < FINAL_RECOMMENDATION_COUNT:
        candidates = supplement_parsed_section_candidates(
            candidates=candidates,
            target_url=target_url,
            section_context=section_context,
            pages=pages,
            discovered_urls=discovered_urls,
            excluded_urls=excluded_urls,
            limit=limit,
        )
    if len(candidates) < FINAL_RECOMMENDATION_COUNT:
        candidates = supplement_global_lexical_urls(
            candidates=candidates,
            target_url=target_url,
            target_profile=target_profile,
            section_context=section_context,
            discovered_urls=discovered_urls,
            pages=pages,
            excluded_urls=excluded_urls,
            limit=limit,
        )
    if len(candidates) < FINAL_RECOMMENDATION_COUNT:
        candidates = supplement_discovered_section_urls(
            candidates=candidates,
            target_url=target_url,
            section_context=section_context,
            discovered_urls=discovered_urls,
            pages=pages,
            excluded_urls=excluded_urls,
            limit=limit,
        )
    candidates = supplement_structural_candidates(
        candidates=candidates,
        target_url=target_url,
        section_context=section_context,
        excluded_urls=excluded_urls,
        limit=limit,
    )
    if not candidates:
        fallback = choose_best_effort_candidate(
            target_url=target_url,
            target_profile=target_profile,
            target_branch_profile=target_branch_profile,
            section_context=section_context,
            pages=pages,
            discovered_urls=discovered_urls,
            excluded_urls=excluded_urls,
        )
        if fallback is not None:
            candidates = [fallback]
    return candidates[:limit]


def is_reachable_donor(page: Page | None) -> bool:
    return page is not None and page.depth is not None and page.depth <= DONOR_MAX_DEPTH


def supplement_structural_candidates(
    *,
    candidates: list[Candidate],
    target_url: str,
    section_context: SectionContext,
    excluded_urls: set[str],
    limit: int,
) -> list[Candidate]:
    if len(candidates) >= limit or has_weak_fallback(candidates):
        return candidates[:limit]

    existing_urls = {candidate.url for candidate in candidates}
    added = 0
    for url in target_parent_urls(target_url):
        if len(candidates) >= limit or added >= 1:
            break
        if url in existing_urls or should_skip_url(url, target_url, excluded_urls):
            continue
        if is_direct_target_parent(url, target_url):
            continue
        label = structural_hub_label(url)
        estimated_depth = estimated_structural_depth(url)
        if estimated_depth > DONOR_MAX_DEPTH:
            continue
        profile = page_token_profile(Page(url=url, title=label, h1=label, depth=estimated_depth), url)
        tier, tier_reason = section_tier(url, Page(url=url, title=label, h1=label), section_context, profile)
        if tier > 2:
            continue
        topic_overlap = profile.strong & section_context.topic_terms
        if not topic_overlap:
            continue
        score = (
            section_tier_bonus(tier)
            + parent_prefix_bonus(url, target_url)
            + len(profile.strong & section_context.series_terms) * 3
            + len(topic_overlap) * 6
        )
        candidates.append(
            Candidate(
                url=url,
                title=label,
                h1=label,
                parent_section=parent_section_from_url(url),
                depth=estimated_depth,
                internal_link_count=0,
                score=round(score, 3),
                reason=(
                    f"section structural hub; tier={tier} {tier_reason}; evidence=hub_with_topic_support; "
                    + "topic: "
                    + ", ".join(sorted(topic_overlap)[:8])
                ),
                source="section_hub",
                confidence="low",
            )
        )
        existing_urls.add(url)
        added += 1
    return candidates[:limit]


def has_weak_fallback(candidates: list[Candidate]) -> bool:
    return any(candidate.source in {"section_hub", "best_effort"} for candidate in candidates)


def supplement_discovered_section_urls(
    *,
    candidates: list[Candidate],
    target_url: str,
    section_context: SectionContext,
    discovered_urls: set[str],
    pages: dict[str, Page],
    excluded_urls: set[str],
    limit: int,
) -> list[Candidate]:
    existing_urls = {candidate.url for candidate in candidates}
    additions: list[Candidate] = []
    for url in discovered_urls:
        if url in existing_urls or url in pages:
            continue
        if should_skip_url(url, target_url, excluded_urls):
            continue
        if is_parent_path(path_parts(url), path_parts(target_url)) and not is_direct_target_parent(url, target_url):
            continue

        score, reason = discovered_section_url_score(
            candidate_url=url,
            target_url=target_url,
            section_context=section_context,
        )
        if score <= 0:
            continue
        label = structural_hub_label(url)
        additions.append(
            Candidate(
                url=url,
                title=label,
                h1=label,
                parent_section=parent_section_from_url(url),
                depth=estimated_structural_depth(url),
                internal_link_count=0,
                score=round(score, 3),
                reason=reason,
                source="section_url",
                confidence="low",
            )
        )

    additions.sort(key=candidate_sort_key)
    added_section_urls = 0
    for candidate in additions:
        if len(candidates) >= limit:
            break
        if candidate.url in existing_urls:
            continue
        if candidate.source == "section_url" and added_section_urls >= 3:
            break
        candidates.append(candidate)
        existing_urls.add(candidate.url)
        if candidate.source == "section_url":
            added_section_urls += 1
    candidates.sort(key=candidate_sort_key)
    return candidates[:limit]


def supplement_global_lexical_urls(
    *,
    candidates: list[Candidate],
    target_url: str,
    target_profile,
    section_context: SectionContext,
    discovered_urls: set[str],
    pages: dict[str, Page],
    excluded_urls: set[str],
    limit: int,
) -> list[Candidate]:
    existing_urls = {candidate.url for candidate in candidates}
    additions: list[Candidate] = []
    for url in discovered_urls | set(pages):
        if url in existing_urls:
            continue
        if should_skip_url(url, target_url, excluded_urls):
            continue
        page = pages.get(url)
        score, reason = global_lexical_url_score(
            candidate_url=url,
            page=page,
            target_profile=target_profile,
            section_context=section_context,
        )
        if score <= 0:
            continue
        label = (page.h1 or page.title) if page is not None else structural_hub_label(url)
        additions.append(
            Candidate(
                url=url,
                title=label,
                h1=label,
                parent_section=page.parent_section if page else parent_section_from_url(url),
                depth=page.depth if page and page.depth is not None else estimated_structural_depth(url),
                internal_link_count=page.internal_link_count if page else 0,
                score=round(score, 3),
                reason=reason,
                source="lexical_reserve",
                confidence="normal" if page is not None else "low",
            )
        )

    additions.sort(key=candidate_sort_key)
    added = 0
    max_additions = max(limit - len(candidates), 0)
    for candidate in additions:
        if len(candidates) >= limit:
            break
        if candidate.url in existing_urls:
            continue
        if added >= max_additions:
            break
        candidates.append(candidate)
        existing_urls.add(candidate.url)
        added += 1
    candidates.sort(key=candidate_sort_key)
    return candidates[:limit]


def global_lexical_url_score(*, candidate_url: str, page: Page | None, target_profile, section_context: SectionContext) -> tuple[float, str]:
    depth = page.depth if page and page.depth is not None else estimated_structural_depth(candidate_url)
    if depth > DONOR_MAX_DEPTH:
        return 0.0, ""
    label = (page.h1 or page.title) if page is not None else structural_hub_label(candidate_url)
    profile = page_token_profile(page or Page(url=candidate_url, title=label, h1=label, depth=depth), candidate_url)
    target_topic_terms = set(section_context.topic_terms)
    target_model_terms = set(target_profile.model_like)
    target_lexical_terms = target_topic_terms | target_model_terms
    topical_overlap = target_topic_terms & profile.topical
    model_overlap = target_profile.model_like & profile.model_like
    heading_overlap = (target_lexical_terms & profile.strong) - topical_overlap - model_overlap
    overlap_count = len(topical_overlap | model_overlap)
    # Do not treat broad brand/category pages as good reserve candidates
    # when they match by only one brand/model token like "apple".
    if overlap_count < 1 and len(heading_overlap) < 2:
        return 0.0, ""
    if len(model_overlap) == 1 and not topical_overlap and len(heading_overlap) == 0:
        return 0.0, ""
    if overlap_count + len(heading_overlap) < 2:
        return 0.0, ""
    penalties = url_penalty(candidate_url)
    parsed_bonus = 3.0 if page is not None else 0.0
    score = len(topical_overlap) * 9.0 + len(model_overlap) * 12.0 + len(heading_overlap) * 4.0 + parsed_bonus - penalties
    if score <= 0:
        return 0.0, ""
    parts = [
        "global lexical fallback",
        "evidence=topic",
        f"parsed={'yes' if page is not None else 'no'}",
        f"topical={len(topical_overlap)}",
        f"heading={len(heading_overlap)}",
    ]
    if topical_overlap:
        parts.append("topic: " + ", ".join(sorted(topical_overlap)[:8]))
    if model_overlap:
        parts.append("models: " + ", ".join(sorted(model_overlap)[:8]))
    if heading_overlap:
        parts.append("heading: " + ", ".join(sorted(heading_overlap)[:8]))
    return score, "; ".join(parts)


def discovered_section_url_score(
    *,
    candidate_url: str,
    target_url: str,
    section_context: SectionContext,
) -> tuple[float, str]:
    label = structural_hub_label(candidate_url)
    depth = estimated_structural_depth(candidate_url)
    if depth > DONOR_MAX_DEPTH:
        return 0.0, ""
    page = Page(url=candidate_url, title=label, h1=label, depth=depth)
    profile = page_token_profile(page, candidate_url)
    tier, tier_reason = section_tier(candidate_url, page, section_context, profile)
    if tier > 4:
        return 0.0, ""

    branch_profile = branch_token_profile(candidate_url)
    candidate_terms = profile.strong | branch_profile.strong
    topic_overlap = candidate_terms & section_context.topic_terms
    series_overlap = candidate_terms & section_context.series_terms
    parent_overlap = candidate_terms & section_context.parent_terms
    slug_specificity = candidate_slug_specificity(candidate_url)
    shared_prefix = common_path_prefix_len(candidate_url, target_url)
    target_depth = estimated_structural_depth(target_url)

    strong_parent_overlap = len(parent_overlap) >= 2

    if topic_overlap:
        relation_bonus = 18.0 if tier == 1 else 12.0 if tier == 2 else 8.0 if tier == 3 else 5.0
    elif strong_parent_overlap and tier <= 4:
        relation_bonus = 4.0
    elif tier == 1 and shared_prefix >= max(3, target_depth - 2) and len(series_overlap | parent_overlap) >= 2:
        relation_bonus = 8.0
    else:
        relation_bonus = 0.0

    if relation_bonus <= 0:
        return 0.0, ""
    if not topic_overlap and not strong_parent_overlap and tier != 1:
        return 0.0, ""
    if not topic_overlap and strong_parent_overlap and tier <= 2 and slug_specificity < 2:
        return 0.0, ""

    topic_score = len(topic_overlap) * 12.0
    series_score = len(series_overlap | parent_overlap) * 2.5
    depth_penalty = max(depth - 5, 0) * 1.5
    structural_penalty = 10.0 if not topic_overlap else 0.0
    score = relation_bonus + section_tier_bonus(tier) + topic_score + series_score - depth_penalty - structural_penalty
    parts = [
        f"discovered section url; tier={tier} {tier_reason}",
        f"evidence={'topic' if topic_overlap else 'parent_topic_support' if strong_parent_overlap else 'structural_fallback'}",
        f"shared_path={shared_prefix}",
        f"topic={topic_score:.1f}",
        f"series={series_score:.1f}",
    ]
    if topic_overlap:
        parts.append("topic: " + ", ".join(sorted(topic_overlap)[:8]))
    if series_overlap:
        parts.append("series: " + ", ".join(sorted(series_overlap)[:8]))
    if parent_overlap:
        parts.append("parent: " + ", ".join(sorted(parent_overlap)[:8]))
    return score, "; ".join(parts)


def candidate_slug_specificity(candidate_url: str) -> int:
    parts = path_parts(candidate_url)
    if not parts:
        return 0
    profile = token_profile_from_text(parts[-1].replace("-", " ").replace("_", " "), domain_generics=domain_tokens(candidate_url), keep_numbers=True)
    return len(profile.strong)


def supplement_parsed_section_candidates(
    *,
    candidates: list[Candidate],
    target_url: str,
    section_context: SectionContext,
    pages: dict[str, Page],
    discovered_urls: set[str],
    excluded_urls: set[str],
    limit: int,
) -> list[Candidate]:
    existing_urls = {candidate.url for candidate in candidates}
    additions: list[Candidate] = []
    for url in discovered_urls | set(pages):
        page = pages.get(url)
        if page is None or url in existing_urls:
            continue
        if should_skip_url(url, target_url, excluded_urls):
            continue
        if page.depth is None or page.depth > DONOR_MAX_DEPTH or page.depth == 0:
            continue
        if is_direct_target_parent(url, target_url):
            continue
        profile = page_token_profile(page, url)
        tier, tier_reason = section_tier(url, page, section_context, profile)
        if tier > 4:
            continue
        score, reason = parsed_section_candidate_score(
            candidate_url=url,
            page=page,
            section_context=section_context,
            profile=profile,
            tier=tier,
            tier_reason=tier_reason,
        )
        if score <= 0:
            continue
        additions.append(
            Candidate(
                url=url,
                title=page.title,
                h1=page.h1,
                parent_section=page.parent_section,
                depth=page.depth,
                internal_link_count=page.internal_link_count,
                score=round(score, 3),
                reason=reason,
                source="parsed_section",
                confidence="normal" if "evidence=topic" in reason else "low",
            )
        )

    additions.sort(key=candidate_sort_key)
    for candidate in additions:
        if len(candidates) >= limit:
            break
        if candidate.url in existing_urls:
            continue
        candidates.append(candidate)
        existing_urls.add(candidate.url)
    candidates.sort(key=candidate_sort_key)
    return candidates[:limit]


def choose_best_effort_candidate(
    *,
    target_url: str,
    target_profile,
    target_branch_profile,
    section_context: SectionContext,
    pages: dict[str, Page],
    discovered_urls: set[str],
    excluded_urls: set[str],
) -> Candidate | None:
    best: Candidate | None = None
    for url in discovered_urls | set(pages):
        page = pages.get(url)
        if page is None:
            continue
        if should_skip_url(url, target_url, excluded_urls):
            continue
        if page.depth is None or page.depth > DONOR_MAX_DEPTH:
            continue
        if page.depth == 0:
            continue
        if is_near_target_parent(url, target_url):
            continue
        score, reason = best_effort_score_candidate(
            candidate_url=url,
            page=page,
            target_url=target_url,
            target_profile=target_profile,
            target_branch_profile=target_branch_profile,
            section_context=section_context,
        )
        if score <= 0:
            continue
        candidate = Candidate(
            url=url,
            title=page.title,
            h1=page.h1,
            parent_section=page.parent_section,
            depth=page.depth,
            internal_link_count=page.internal_link_count,
            score=round(score, 3),
            reason=reason,
            source="best_effort",
            confidence="low",
        )
        if best is None or candidate_sort_key(candidate) < candidate_sort_key(best):
            best = candidate
    return best


def should_skip_url(url: str, target_url: str, excluded_urls: set[str]) -> bool:
    if same_url(url, target_url) or url in excluded_urls:
        return True
    if is_short_alias_of_target_url(url, target_url):
        return True
    parsed = urlsplit(url)
    if parsed.query and len(parsed.query) > 20:
        return True
    parts = {part.casefold() for part in parsed.path.split("/") if part}
    if parts & JUNK_PATH_PARTS:
        return True
    if re.search(r"/page/\d+|[?&](page|p)=\d+", url):
        return True
    return False


def should_skip_page(page: Page, target_url: str) -> bool:
    if page.depth == 0:
        return True
    if is_blocked_page(page):
        return True
    profile = page_token_profile(page, page.url)
    if not profile.strong and not profile.location:
        return True
    if is_general_shell_page(page.url, page, profile):
        return True
    target_profile = page_token_profile(None, target_url)
    if unrelated_vertical_penalty(target_profile, profile) >= 20.0:
        return True
    return False


def is_short_alias_of_target_branch(candidate_url: str, target_url: str) -> bool:
    candidate_parts = [part for part in urlsplit(candidate_url).path.split("/") if part]
    target_parts = [part for part in urlsplit(target_url).path.split("/") if part]
    if not candidate_parts or len(candidate_parts) > 2:
        return False
    if candidate_parts[0] in {"rubric", "rubrics", "category", "categories", "topic", "topics"}:
        return False
    return candidate_parts[-1] in set(target_parts[:-1])


def is_short_alias_of_target_url(candidate_url: str, target_url: str) -> bool:
    candidate_parts = [part.casefold() for part in urlsplit(candidate_url).path.split("/") if part]
    target_parts = [part.casefold() for part in urlsplit(target_url).path.split("/") if part]
    if not candidate_parts or not target_parts:
        return False
    if len(candidate_parts) >= len(target_parts):
        return False
    if candidate_parts[0] in {"rubric", "rubrics", "category", "categories", "topic", "topics"}:
        return False
    return candidate_parts == target_parts[-len(candidate_parts):]
