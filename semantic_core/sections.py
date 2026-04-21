from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from models import Page

from .profiles import (
    TokenProfile,
    branch_token_profile,
    domain_tokens,
    page_token_profile,
    token_profile_from_text,
)


@dataclass(frozen=True)
class SectionContext:
    target_url: str
    target_parts: list[str]
    topic_terms: set[str]
    series_terms: set[str]
    parent_terms: set[str]


def build_section_context(target_url: str, target_page: Page | None) -> SectionContext:
    parts = path_parts(target_url)
    topic_profile = token_profile_from_text(
        " ".join(
            [
                parts[-1].replace("-", " ") if parts else "",
                target_page.title if target_page else "",
                target_page.h1 if target_page else "",
            ]
        ),
        domain_generics=domain_tokens(target_url),
        keep_numbers=True,
    )
    branch_profiles = [
        token_profile_from_text(part.replace("-", " "), domain_generics=domain_tokens(target_url), keep_numbers=True)
        for part in parts[:-1]
    ]
    series_terms: set[str] = set()
    parent_terms: set[str] = set()
    for index, profile in enumerate(branch_profiles):
        strong = profile.strong
        if not strong:
            continue
        if index >= max(0, len(branch_profiles) - 5):
            series_terms.update(strong)
        if index >= max(0, len(branch_profiles) - 3):
            parent_terms.update(strong)
    if target_page and target_page.breadcrumbs:
        breadcrumb_profile = token_profile_from_text(
            " ".join(target_page.breadcrumbs),
            domain_generics=domain_tokens(target_url),
            keep_numbers=True,
        )
        series_terms.update(breadcrumb_profile.strong)
        parent_terms.update(breadcrumb_profile.strong & series_terms)
    return SectionContext(
        target_url=target_url,
        target_parts=parts,
        topic_terms=topic_profile.strong,
        series_terms=series_terms - topic_profile.strong,
        parent_terms=parent_terms,
    )


def section_tier(
    candidate_url: str,
    page: Page | None,
    section_context: SectionContext,
    candidate_profile: TokenProfile,
) -> tuple[int, str]:
    candidate_parts = path_parts(candidate_url)
    if not candidate_parts or not section_context.target_parts:
        return 7, "unrelated"
    if candidate_parts == section_context.target_parts:
        return 9, "target"

    shared_prefix = common_path_prefix_parts(candidate_parts, section_context.target_parts)
    candidate_branch_profile = branch_token_profile(candidate_url)
    candidate_terms = candidate_profile.strong | candidate_branch_profile.strong
    series_overlap = candidate_terms & section_context.series_terms
    topic_overlap = candidate_terms & section_context.topic_terms
    parent_overlap = candidate_terms & section_context.parent_terms

    if is_sibling_path(candidate_parts, section_context.target_parts) and (
        topic_overlap or len(series_overlap | parent_overlap) >= 1
    ):
        return 1, "exact_sibling"
    if is_parent_path(candidate_parts, section_context.target_parts) and (
        topic_overlap or len(series_overlap | parent_overlap) >= 2
    ):
        return 2, "thematic_hub"
    if shared_prefix >= max(3, len(section_context.target_parts) - 3) and (
        topic_overlap or len(series_overlap) >= 2
    ):
        return 3, "same_section_cluster"
    if shared_prefix >= 2 and (len(series_overlap | parent_overlap) >= 2 or len(topic_overlap) >= 1):
        return 4, "broader_parent_cluster"
    if len(topic_overlap) >= 2:
        return 5, "topical_cross_section"
    if shared_prefix >= 2 and (series_overlap or parent_overlap):
        return 6, "generic_branch_near"
    return 7, "unrelated"


def section_tier_bonus(tier: int) -> float:
    if tier == 1:
        return 40.0
    if tier == 2:
        return 26.0
    if tier == 3:
        return 12.0
    if tier == 4:
        return 4.0
    if tier == 5:
        return 8.0
    return 0.0


def path_parts(url: str) -> list[str]:
    return [part for part in urlsplit(url).path.split("/") if part]


def common_path_prefix_parts(left_parts: list[str], right_parts: list[str]) -> int:
    shared = 0
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part != right_part:
            break
        shared += 1
    return shared


def is_sibling_path(candidate_parts: list[str], target_parts: list[str]) -> bool:
    return (
        len(candidate_parts) == len(target_parts)
        and len(candidate_parts) >= 2
        and candidate_parts[:-1] == target_parts[:-1]
        and candidate_parts[-1] != target_parts[-1]
    )


def is_parent_path(candidate_parts: list[str], target_parts: list[str]) -> bool:
    return bool(candidate_parts and len(candidate_parts) < len(target_parts) and target_parts[: len(candidate_parts)] == candidate_parts)


def parent_prefix_bonus(candidate_url: str, target_url: str) -> float:
    candidate_path = urlsplit(candidate_url).path.rstrip("/")
    target_parts = path_parts(target_url)
    for rank, index in enumerate(range(len(target_parts) - 1, 0, -1), start=1):
        parent_path = "/" + "/".join(target_parts[:index])
        if candidate_path == parent_path:
            return max(10.0 - rank, 3.0)
        if candidate_path.startswith(parent_path + "/"):
            return max(6.0 - rank, 1.0)
    return 0.0


def is_near_target_parent(candidate_url: str, target_url: str) -> bool:
    candidate_parts = path_parts(candidate_url)
    target_parts = path_parts(target_url)
    if len(target_parts) < 4:
        return False
    return candidate_parts in (target_parts[:-1], target_parts[:-2])


def is_direct_target_parent(candidate_url: str, target_url: str) -> bool:
    candidate_parts = path_parts(candidate_url)
    target_parts = path_parts(target_url)
    return bool(target_parts and candidate_parts == target_parts[:-1])


def parent_section_from_url(url: str) -> str:
    parts = path_parts(url)
    if len(parts) <= 1:
        return ""
    return parts[-2].replace("-", " ").replace("_", " ")


def structural_hub_label(url: str) -> str:
    parts = path_parts(url)
    if not parts:
        return ""
    cleaned = parts[-1].replace("-", " ").replace("_", " ")
    words = []
    for word in cleaned.split():
        if word.isupper():
            words.append(word)
        elif len(word) <= 4 and word in {"glri", "noaa"}:
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


def target_parent_urls(target_url: str) -> list[str]:
    parsed = urlsplit(target_url)
    parts = path_parts(target_url)
    parents: list[str] = []
    for index in range(len(parts) - 1, 0, -1):
        parent = f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts[:index])}"
        if parent not in parents:
            parents.append(parent)
    return parents


def estimated_structural_depth(url: str) -> int:
    return len(path_parts(url))


def common_path_prefix_len(left: str, right: str) -> int:
    return common_path_prefix_parts(path_parts(left), path_parts(right))


def same_url(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/")
