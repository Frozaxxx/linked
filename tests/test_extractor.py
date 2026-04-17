from __future__ import annotations

from app.services.parser import ExtractionRule, extract_fields


def test_extract_fields_supports_text_attributes_and_fallback_selectors() -> None:
    html = """
    <html>
      <body>
        <article><a href="/news/a">First story</a></article>
        <article><a href="/news/b">Second story</a></article>
      </body>
    </html>
    """

    result = extract_fields(
        html,
        requested_url="https://example.com/",
        final_url="https://example.com/",
        rules=[
            ExtractionRule(name="stories", selector=".missing", fallback_selectors=["article"], multiple=True),
            ExtractionRule(name="first_link", selector="article a", attr="href", multiple=False),
        ],
    )

    assert result.fields["stories"].selector_used == "article"
    assert [match.text for match in result.fields["stories"].matches] == ["First story", "Second story"]
    assert result.fields["first_link"].matches[0].value == "https://example.com/news/a"
