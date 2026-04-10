from __future__ import annotations

from types import SimpleNamespace

from app.services.link_placement_models import PlacementRecommendation
from app.services.llm_summary_templates import build_static_message


def make_recommendation(
    url: str,
    *,
    depth: int,
    projected_steps: int | None,
    confidence: str = "strong",
    reason: str = "ключевые слова URL: target, page",
) -> PlacementRecommendation:
    return PlacementRecommendation(
        source_url=url,
        source_title=None,
        source_depth=depth,
        projected_steps_to_target=projected_steps,
        reason=reason,
        placement_hint="Лучше разместить ссылку в основном контенте страницы",
        anchor_hint="Target page",
        confidence=confidence,
    )


def make_context(**overrides) -> SimpleNamespace:
    payload = {
        "start_url": "https://example.com/",
        "target_url": "https://example.com/target",
        "target_title": "Target page",
        "found": False,
        "optimization_status": "bad",
        "steps_to_target": None,
        "good_depth_threshold": 4,
        "search_depth_limit": 4,
        "matched_by": [],
        "pages_fetched": 5,
        "pages_discovered": 12,
        "sitemap_checked": True,
        "found_in_sitemap": False,
        "html_fetch_mode": "playwright",
        "sitemap_fetch_mode": "http-only",
        "crawl_max_depth": 4,
        "budget_exhausted": False,
        "depth_cutoff": False,
        "level_truncated": False,
        "truncated_levels": 0,
        "truncated_nodes": 0,
        "path": [],
        "placement_recommendations": [],
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_strong_candidate_message_contains_reason_and_best_url() -> None:
    recommendation = make_recommendation(
        "https://example.com/section/best-donor",
        depth=2,
        projected_steps=3,
        reason="ключевые слова URL: target, page, guide",
    )
    context = make_context(
        found=True,
        steps_to_target=6,
        placement_recommendations=[recommendation],
    )

    message = build_static_message(context)

    assert message is not None
    assert message.count(recommendation.source_url) == 1
    assert "Лучший семантически близкий URL для ссылки" in message
    assert "Почему выбран именно он" in message
    assert "После добавления ссылки путь до цели сократится до 3 шагов" in message


def test_soft_candidates_message_is_ranked_and_contains_reasons() -> None:
    recommendations = [
        make_recommendation(
            "https://example.com/candidate-1",
            depth=1,
            projected_steps=2,
            confidence="soft",
            reason="ключевые слова URL: winter, observations, mobile",
        ),
        make_recommendation(
            "https://example.com/candidate-2",
            depth=2,
            projected_steps=3,
            confidence="soft",
            reason="ключевые слова URL: mobile, ocean, robotic",
        ),
        make_recommendation(
            "https://example.com/candidate-3",
            depth=3,
            projected_steps=4,
            confidence="soft",
            reason="ключевые слова URL: winter, ecosystems, monitoring",
        ),
    ]
    context = make_context(placement_recommendations=recommendations)

    message = build_static_message(context)

    assert message is not None
    assert "в порядке приоритета" in message
    assert "1) https://example.com/candidate-1" in message
    assert "2) https://example.com/candidate-2" in message
    assert "3) https://example.com/candidate-3" in message
    assert "ключевые слова URL" in message


def test_fallback_candidates_message_marks_current_crawl_depth_when_truncated() -> None:
    recommendations = [
        make_recommendation(
            "https://example.com/branch",
            depth=1,
            projected_steps=2,
            confidence="fallback",
            reason="общая структурная ветка: GLRI / Focus Area 5",
        ),
        make_recommendation(
            "https://example.com/branch/section",
            depth=2,
            projected_steps=3,
            confidence="fallback",
            reason="ключевые слова URL: winter, observations",
        ),
        make_recommendation(
            "https://example.com/branch/section/topic",
            depth=3,
            projected_steps=4,
            confidence="fallback",
            reason="ключевые слова URL: monitoring, winter",
        ),
    ]
    context = make_context(
        pages_fetched=40,
        pages_discovered=464,
        found_in_sitemap=True,
        level_truncated=True,
        truncated_nodes=298,
        placement_recommendations=recommendations,
    )

    message = build_static_message(context)

    assert message is not None
    assert "Целевая страница есть в sitemap" in message
    assert "Обход был неполным, поэтому это еще не доказывает слабую перелинковку" in message
    assert "глубина в текущем запуске: 1" in message
    assert "первый кандидат наиболее сильный" in message


def test_candidate_reason_label_keeps_url_acronym_uppercase() -> None:
    recommendation = make_recommendation(
        "https://example.com/candidate-1",
        depth=1,
        projected_steps=2,
        confidence="fallback",
        reason="URL этой страницы семантически ближе всего к целевой теме по ключевым словам: mobile, observation",
    )
    context = make_context(
        found_in_sitemap=True,
        level_truncated=True,
        placement_recommendations=[recommendation],
    )

    message = build_static_message(context)

    assert message is not None
    assert "URL этой страницы семантически ближе всего" in message


def test_access_issue_message_reports_playwright_and_http_modes() -> None:
    context = make_context(
        pages_fetched=0,
        pages_discovered=1,
        placement_recommendations=[],
        path=[],
        html_fetch_mode="playwright",
        sitemap_fetch_mode="http-only",
    )

    message = build_static_message(context)

    assert message is not None
    assert "HTML-страницы запрашивались через Playwright" in message
    assert "sitemap проверялся только по HTTP" in message
    assert "иначе сервис не сможет надежно подобрать страницу-донора" in message
