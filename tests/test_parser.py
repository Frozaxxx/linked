from __future__ import annotations

import unittest

from app.services.parser import normalize_url


class NormalizeUrlTests(unittest.TestCase):
    def test_normalize_url_returns_none_for_invalid_port(self) -> None:
        value = normalize_url("https://example.com:%20broken-port/path")

        self.assertIsNone(value)


if __name__ == "__main__":
    unittest.main()
