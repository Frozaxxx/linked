from __future__ import annotations

import unittest

import main
from app.main import app


class EntrypointTests(unittest.TestCase):
    def test_root_main_exports_app(self) -> None:
        self.assertIs(main.app, app)


if __name__ == "__main__":
    unittest.main()
