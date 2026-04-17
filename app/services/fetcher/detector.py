from __future__ import annotations

import re

from app.models import SeoLinkedModel


SCRIPT_RE = re.compile(r"<script\b", re.IGNORECASE)
ANCHOR_RE = re.compile(r"<a\s+[^>]*href\s*=", re.IGNORECASE)
VISIBLE_TEXT_RE = re.compile(r"<(script|style|noscript|template)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
SPA_MARKERS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "id=\"root\"",
    "id='root'",
    "id=\"app\"",
    "id='app'",
    "data-reactroot",
    "ng-version",
    "vite/client",
    "requires javascript",
    "enable javascript",
)


class DynamicDetectionResult(SeoLinkedModel):
    should_render: bool
    reasons: list[str]
    visible_text_length: int
    anchor_count: int
    script_count: int


def detect_dynamic_html(html: str, *, content_type: str = "") -> DynamicDetectionResult:
    normalized_content_type = content_type.lower()
    if normalized_content_type and "html" not in normalized_content_type:
        return DynamicDetectionResult(
            should_render=False,
            reasons=["non-html content-type"],
            visible_text_length=0,
            anchor_count=0,
            script_count=0,
        )

    normalized_html = html.lower()
    visible_text = VISIBLE_TEXT_RE.sub(" ", html)
    visible_text_length = len(" ".join(visible_text.split()))
    anchor_count = len(ANCHOR_RE.findall(html))
    script_count = len(SCRIPT_RE.findall(html))
    reasons: list[str] = []

    if visible_text_length < 300 and (script_count > 0 or anchor_count == 0):
        reasons.append("low visible text")
    if any(marker.lower() in normalized_html for marker in SPA_MARKERS):
        reasons.append("spa marker")
    if script_count >= 8 and anchor_count <= 3:
        reasons.append("script-heavy shell")
    if anchor_count == 0 and script_count > 0 and visible_text_length < 1200:
        reasons.append("no links in script-rendered page")

    return DynamicDetectionResult(
        should_render=bool(reasons),
        reasons=reasons,
        visible_text_length=visible_text_length,
        anchor_count=anchor_count,
        script_count=script_count,
    )
