from __future__ import annotations

import main
from app.main import app


def test_root_main_exports_app() -> None:
    assert main.app is app
