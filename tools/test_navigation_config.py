"""Keep mobile navigation independent of instant page replacement."""

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NavigationConfigTests(unittest.TestCase):
    def setUp(self):
        self.project = tomllib.loads(
            (ROOT / "zensical.toml").read_text(encoding="utf-8")
        )["project"]

    def test_native_navigation_without_instant_or_prefetch(self):
        features = self.project["theme"]["features"]
        self.assertFalse(
            any(feature.startswith("navigation.instant") for feature in features),
            "Mobile links must use native document navigation.",
        )

    def test_standard_navigation_and_search_remain_enabled(self):
        features = self.project["theme"]["features"]
        for feature in ("navigation.indexes", "navigation.path", "navigation.footer"):
            self.assertIn(feature, features)
        self.assertIn("search", self.project["plugins"])

    def test_public_docs_source_is_unchanged(self):
        self.assertEqual(self.project["docs_dir"], "docs")
        self.assertEqual(self.project["site_dir"], "site")


if __name__ == "__main__":
    unittest.main()
