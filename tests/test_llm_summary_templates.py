from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.link_placement_models import PlacementRecommendation
from app.services.llm_summary_templates import build_static_message


def make_recommendation(
    url: str,
    *,
    depth: int,
    projected_steps: int | None,
    confidence: str = "strong",
) -> PlacementRecommendation:
    return PlacementRecommendation(
        source_url=url,
        source_title=None,
        source_depth=depth,
        projected_steps_to_target=projected_steps,
        reason="test",
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
        "path": [],
        "placement_recommendations": [],
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class SummaryTemplateTests(unittest.TestCase):
    def test_strong_candidate_message_contains_single_best_url(self) -> None:
        recommendation = make_recommendation(
            "https://example.com/section/best-donor",
            depth=2,
            projected_steps=3,
        )
        context = make_context(
            found=True,
            steps_to_target=6,
            placement_recommendations=[recommendation],
        )

        message = build_static_message(context)

        self.assertIsNotNone(message)
        self.assertEqual(message.count(recommendation.source_url), 1)
        self.assertIn("Лучший семантически близкий URL для ссылки", message)
        self.assertIn("пользователю сложнее быстро до нее дойти", message)
        self.assertIn("После добавления ссылки путь до цели сократится до 3 шагов", message)

    def test_soft_candidates_message_contains_up_to_three_urls(self) -> None:
        recommendations = [
            make_recommendation("https://example.com/candidate-1", depth=1, projected_steps=2, confidence="soft"),
            make_recommendation("https://example.com/candidate-2", depth=2, projected_steps=3, confidence="soft"),
            make_recommendation("https://example.com/candidate-3", depth=3, projected_steps=4, confidence="soft"),
            make_recommendation("https://example.com/candidate-4", depth=3, projected_steps=4, confidence="soft"),
        ]
        context = make_context(placement_recommendations=recommendations)

        message = build_static_message(context)

        self.assertIsNotNone(message)
        self.assertIn("Сильного семантически точного донора сервис не подтвердил", message)
        self.assertIn("в пределах 1-3 шагов от стартовой страницы", message)
        self.assertIn("https://example.com/candidate-1", message)
        self.assertIn("https://example.com/candidate-2", message)
        self.assertIn("https://example.com/candidate-3", message)
        self.assertNotIn("https://example.com/candidate-4", message)

    def test_fallback_candidates_message_contains_structural_urls(self) -> None:
        recommendations = [
            make_recommendation("https://example.com/branch", depth=1, projected_steps=2, confidence="fallback"),
            make_recommendation("https://example.com/branch/section", depth=2, projected_steps=3, confidence="fallback"),
            make_recommendation("https://example.com/branch/section/topic", depth=3, projected_steps=4, confidence="fallback"),
        ]
        context = make_context(
            pages_fetched=0,
            pages_discovered=1,
            placement_recommendations=recommendations,
        )

        message = build_static_message(context)

        self.assertIsNotNone(message)
        self.assertIn("резервных URL-кандидатов", message)
        self.assertIn("https://example.com/branch", message)
        self.assertIn("https://example.com/branch/section", message)
        self.assertIn("https://example.com/branch/section/topic", message)

    def test_access_issue_message_reports_playwright_and_http_modes(self) -> None:
        context = make_context(
            pages_fetched=0,
            pages_discovered=1,
            placement_recommendations=[],
            path=[],
            html_fetch_mode="playwright",
            sitemap_fetch_mode="http-only",
        )

        message = build_static_message(context)

        self.assertIsNotNone(message)
        self.assertIn("HTML-страницы запрашивались через Playwright", message)
        self.assertIn("sitemap проверялся только по HTTP", message)
        self.assertIn("получать меньше внутреннего веса", message)


if __name__ == "__main__":
    unittest.main()
