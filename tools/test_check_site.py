import tempfile
import unittest
from pathlib import Path

from check_site import audit


class SiteAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name)
        (self.site / "assets").mkdir()
        (self.site / "assets" / "site.css").touch()

    def page(self, content):
        (self.site / "index.html").write_text(content, encoding="utf-8")

    def test_project_site_absolute_asset(self):
        self.page('<link rel="stylesheet" href="/linux-doc/assets/site.css">')
        self.assertEqual(audit(self.site, "/linux-doc/")["missing_files"], [])

    def test_relative_asset(self):
        self.page('<link rel="stylesheet" href="assets/site.css">')
        self.assertEqual(audit(self.site, "/linux-doc/")["missing_files"], [])

    def test_root_site_absolute_asset(self):
        self.page('<link rel="stylesheet" href="/assets/site.css">')
        self.assertEqual(audit(self.site)["missing_files"], [])

    def test_prefix_must_match_complete_path_segment(self):
        self.page('<link rel="stylesheet" href="/linux-doc-other/assets/site.css">')
        issues = audit(self.site, "/linux-doc/")["missing_files"]
        self.assertEqual(issues[0]["target"], "linux-doc-other/assets/site.css")


if __name__ == "__main__":
    unittest.main()
