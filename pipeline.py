from __future__ import annotations

from config import FINAL_RECOMMENDATION_COUNT, GOOD_DEPTH_THRESHOLD, LOCAL_CANDIDATE_LIMIT
from crawler_core.core import crawl_site
from llm import build_final_message, rerank_candidates
from models import AnalysisResult, Candidate, Page
from prompts import fallback_message
from semantic_core.selection import choose_top_candidates


async def analyze(target_url: str) -> AnalysisResult:
    crawl = await crawl_site(target_url)
    excluded_urls = set(crawl.path)
    poor_linking = crawl.steps_to_target is None or crawl.steps_to_target > GOOD_DEPTH_THRESHOLD

    if (
        crawl.target_page is None
        and crawl.target_status is not None
        and crawl.target_status >= 400
        and not crawl.pages
        and not crawl.discovered_urls
    ):
        return AnalysisResult(
            target_url=crawl.target_url,
            target_status=crawl.target_status,
            target_error=crawl.target_error,
            home_url=crawl.home_url,
            found=crawl.found,
            steps_to_target=crawl.steps_to_target,
            path=crawl.path,
            linking_status="target_unavailable",
            poor_linking=True,
            pages_parsed=len(crawl.pages),
            pages_discovered=len(crawl.discovered_urls),
            crawl_stats=crawl.stats,
            robots_checked=crawl.robots_checked,
            robots_available=crawl.robots_available,
            sitemap_checked=crawl.sitemap_checked,
            found_in_sitemap=crawl.found_in_sitemap,
            requested_top_k=LOCAL_CANDIDATE_LIMIT,
            local_candidates_count=0,
            returned_top_k=0,
            diagnostic_reasons=diagnostic_reasons(crawl=crawl, candidates=[]),
            local_top5=[],
            llm_top3=[],
            candidates=[],
            llm_explanation="",
            rerank_source="not-needed: target unavailable",
            message=target_unavailable_message(crawl.target_status, crawl.target_error),
        )

    if not poor_linking:
        return AnalysisResult(
            target_url=crawl.target_url,
            target_status=crawl.target_status,
            target_error=crawl.target_error,
            home_url=crawl.home_url,
            found=crawl.found,
            steps_to_target=crawl.steps_to_target,
            path=crawl.path,
            linking_status="good",
            poor_linking=False,
            pages_parsed=len(crawl.pages),
            pages_discovered=len(crawl.discovered_urls),
            crawl_stats=crawl.stats,
            robots_checked=crawl.robots_checked,
            robots_available=crawl.robots_available,
            sitemap_checked=crawl.sitemap_checked,
            found_in_sitemap=crawl.found_in_sitemap,
            requested_top_k=LOCAL_CANDIDATE_LIMIT,
            local_candidates_count=0,
            returned_top_k=0,
            diagnostic_reasons=diagnostic_reasons(crawl=crawl, candidates=[]),
            local_top5=[],
            llm_top3=[],
            candidates=[],
            llm_explanation="",
            rerank_source="not-needed: good linking",
            message=fallback_message(
                poor_linking=False,
                steps_to_target=crawl.steps_to_target,
                good_depth=GOOD_DEPTH_THRESHOLD,
                has_candidates=False,
            ),
        )

    target_page = crawl.target_page or synthetic_target_page(crawl.target_url)
    local_candidates = choose_top_candidates(
        target_url=crawl.target_url,
        target_page=target_page,
        pages=crawl.pages,
        discovered_urls=crawl.discovered_urls,
        excluded_urls=excluded_urls,
    )
    llm_top3, rerank_source, llm_explanation = await rerank_candidates(
        target_url=crawl.target_url,
        candidates=local_candidates,
    )

    result = AnalysisResult(
        target_url=crawl.target_url,
        target_status=crawl.target_status,
        target_error=crawl.target_error,
        home_url=crawl.home_url,
        found=crawl.found,
        steps_to_target=crawl.steps_to_target,
        path=crawl.path,
        linking_status="bad" if poor_linking else "good",
        poor_linking=poor_linking,
        pages_parsed=len(crawl.pages),
        pages_discovered=len(crawl.discovered_urls),
        crawl_stats=crawl.stats,
        robots_checked=crawl.robots_checked,
        robots_available=crawl.robots_available,
        sitemap_checked=crawl.sitemap_checked,
        found_in_sitemap=crawl.found_in_sitemap,
        requested_top_k=LOCAL_CANDIDATE_LIMIT,
        local_candidates_count=len(local_candidates),
        returned_top_k=min(len(llm_top3), FINAL_RECOMMENDATION_COUNT),
        diagnostic_reasons=diagnostic_reasons(crawl=crawl, candidates=llm_top3),
        local_top5=local_candidates,
        llm_top3=llm_top3,
        candidates=llm_top3,
        llm_explanation=llm_explanation,
        rerank_source=rerank_source,
        message=fallback_message(
            poor_linking=poor_linking,
            steps_to_target=crawl.steps_to_target,
            good_depth=GOOD_DEPTH_THRESHOLD,
            has_candidates=bool(llm_top3),
        ),
    )
    result.message, message_source = await build_final_message(result)
    result.message = with_candidate_explanations(result.message, result.candidates)
    if message_source != "llm" and result.rerank_source == "llm":
        result.rerank_source = f"{result.rerank_source}; message {message_source}"
    elif message_source != "llm":
        result.rerank_source = f"{result.rerank_source}; message {message_source}"
    return result


def target_unavailable_message(status: int | None, error: str) -> str:
    if status is not None and status >= 400:
        return (
            f"Целевая страница сейчас недоступна: сайт вернул HTTP {status}. "
            "Анализ остановлен, потому что для подбора внутренних ссылок сначала нужно, чтобы целевой URL открывался без ошибки."
        )
    if error:
        return (
            "Целевую страницу не удалось проверить без ошибки. "
            "Анализ остановлен только потому, что сайт вернул явную ошибку доступности."
        )
    return "Целевую страницу не удалось проверить. Анализ остановлен только при явной ошибке доступности."


def synthetic_target_page(target_url: str) -> Page:
    return Page(url=target_url, title="", h1="", parent_section="")


def with_candidate_explanations(message: str, candidates: list[Candidate]) -> str:
    if not candidates:
        return message

    missing_candidates = [candidate for candidate in candidates[:FINAL_RECOMMENDATION_COUNT] if candidate.url not in message]
    if not missing_candidates:
        return message

    lines = ["Рекомендованные кандидаты:"]
    for index, candidate in enumerate(missing_candidates, start=1):
        reason = readable_candidate_reason(candidate)
        lines.append(f"{index}. {candidate.url} - {reason}.")
    return f"{message.rstrip()} {' '.join(lines)}"


def diagnostic_reasons(*, crawl, candidates: list[Candidate]) -> list[str]:
    reasons: list[str] = []
    if not crawl.found:
        reasons.append("target_not_found_in_crawl_path")
    if candidates and not any(candidate.source == "parsed" and candidate.confidence in {"normal", "high"} for candidate in candidates):
        reasons.append("no_confirmed_strong_content_donor")
    if any(candidate.source == "section_hub" for candidate in candidates):
        reasons.append("section_hub_used")
    if any(candidate.source == "section_url" for candidate in candidates):
        reasons.append("section_url_used")
    if any(candidate.source == "lexical_reserve" for candidate in candidates):
        reasons.append("lexical_reserve_used")
    if all(candidate.confidence == "low" for candidate in candidates) and candidates:
        reasons.append("only_low_confidence_candidates")
    if crawl.stats.goto_ok and crawl.stats.html_length_gt_threshold < crawl.stats.goto_ok:
        reasons.append("some_opened_pages_did_not_produce_stable_html")
    return reasons


def readable_candidate_reason(candidate: Candidate) -> str:
    label = candidate.h1 or candidate.title
    structural_only = "evidence=structural_fallback" in candidate.reason
    if candidate.source == "best_effort":
        return "слабый запасной вариант: строгий тематический отбор не нашел более точных доноров"
    if candidate.source == "section_hub":
        return "структурно близкий hub той же ветки; использовать стоит осторожно, если сильных тематических доноров мало"
    if candidate.source == "section_url":
        return "мягкий fallback из той же ветки URL без полного подтверждения контента; это скорее структурно близкий вариант, чем сильный тематический донор"
    if candidate.source == "lexical_reserve":
        return "резервный кандидат со всего сайта, найденный по лексической близости URL или заголовка к теме целевой страницы"
    if candidate.source == "parsed_section" and structural_only:
        return "распарсенная страница из близкой ветки, но с ограниченным тематическим совпадением"
    if structural_only:
        return "страница выбрана в основном по близости ветки и структуры, а не по сильному тематическому совпадению"
    if label:
        return f"тематически близкая страница: {label}"
    if candidate.parent_section:
        return f"страница из близкого раздела: {candidate.parent_section}"
    return "страница выбрана по сочетанию темы и структуры без сильного fallback-сигнала"
