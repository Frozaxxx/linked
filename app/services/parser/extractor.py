from __future__ import annotations

from bs4 import BeautifulSoup
from pydantic import Field

from app.models import SeoLinkedModel
from app.services.parser.urls import normalize_url


class ExtractionRule(SeoLinkedModel):
    name: str = "items"
    selector: str
    fallback_selectors: list[str] = Field(default_factory=list)
    attr: str | None = None
    multiple: bool = True


class ExtractionMatch(SeoLinkedModel):
    selector: str
    value: str
    text: str
    attrs: dict[str, str]


class ExtractionFieldResult(SeoLinkedModel):
    name: str
    selector_used: str | None
    matches: list[ExtractionMatch]


class ExtractionResult(SeoLinkedModel):
    url: str
    final_url: str
    fields: dict[str, ExtractionFieldResult]


def extract_fields(html: str, *, requested_url: str, final_url: str, rules: list[ExtractionRule]) -> ExtractionResult:
    soup = BeautifulSoup(html, "lxml")
    fields: dict[str, ExtractionFieldResult] = {}
    for rule in rules:
        selector_used: str | None = None
        elements = []
        for selector in [rule.selector, *rule.fallback_selectors]:
            elements = soup.select(selector)
            if elements:
                selector_used = selector
                break
        if not rule.multiple:
            elements = elements[:1]

        matches: list[ExtractionMatch] = []
        for element in elements:
            attrs = {str(key): " ".join(value) if isinstance(value, list) else str(value) for key, value in element.attrs.items()}
            value = element.get_text(separator=" ", strip=True)
            if rule.attr:
                value = attrs.get(rule.attr, "")
                if rule.attr in {"href", "src"}:
                    value = normalize_url(value, final_url, allow_ignored_extensions=True) or value
            matches.append(
                ExtractionMatch(
                    selector=selector_used or rule.selector,
                    value=value,
                    text=element.get_text(separator=" ", strip=True),
                    attrs=attrs,
                )
            )

        fields[rule.name] = ExtractionFieldResult(
            name=rule.name,
            selector_used=selector_used,
            matches=matches,
        )

    return ExtractionResult(url=requested_url, final_url=final_url, fields=fields)
