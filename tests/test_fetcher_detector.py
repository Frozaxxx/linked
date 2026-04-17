from __future__ import annotations

from app.services.fetcher.detector import detect_dynamic_html


def test_detector_does_not_render_content_rich_static_html() -> None:
    html = "<html><body><h1>Title</h1>" + ("Visible text " * 80) + "<a href='/a'>A</a></body></html>"

    result = detect_dynamic_html(html, content_type="text/html")

    assert result.should_render is False
    assert result.reasons == []


def test_detector_marks_spa_shell_as_dynamic() -> None:
    html = """
    <html>
      <body>
        <div id="root"></div>
        <script src="/app.js"></script>
        <script>window.__NEXT_DATA__ = {}</script>
      </body>
    </html>
    """

    result = detect_dynamic_html(html, content_type="text/html")

    assert result.should_render is True
    assert "spa marker" in result.reasons
