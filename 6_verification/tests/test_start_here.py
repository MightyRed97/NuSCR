"""START_HERE.html is current, embeds the current browser engine, links only to files that exist, and its operator
actions work in a real browser (tools/check_page.js, when Node.js and Chrome or Edge are installed)."""
import os
import re
import shutil
import subprocess
import unittest
from html import unescape

import helpers
from design_model import load_design
from paths import JS_ENGINE, ROOT, START_HERE
from render_start_here import page_text

NODE = shutil.which("node")
BROWSER = any(os.path.exists(p) for p in (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"))


class StartPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = START_HERE.read_text(encoding="utf-8")

    def test_page_is_current(self):
        self.assertEqual(self.page, page_text(load_design(spec=helpers.spec())))

    def test_embeds_the_engine_and_loads_nothing_else(self):
        self.assertIn(JS_ENGINE.read_text(encoding="utf-8"), self.page)
        self.assertNotRegex(self.page, r"<script[^>]+src=|<link[^>]+href=|https?://(?!www\.w3\.org)")

    def test_every_local_link_exists(self):
        links = set(re.findall(r'href="([^"#:]+)"', self.page))
        links |= set(re.findall(r'"(\d_[A-Za-z_]+/[^"]+?)"', self.page))
        self.assertTrue(links)
        missing = [p for p in sorted(links) if not (ROOT / unescape(p)).exists()]
        self.assertEqual(missing, [])

    @unittest.skipUnless(NODE and BROWSER, "needs Node.js and Chrome or Edge")
    def test_operator_actions_in_a_browser(self):
        r = subprocess.run([NODE, str(ROOT / "tools" / "check_page.js"), str(START_HERE)], capture_output=True,
                           text=True, timeout=300)
        if r.returncode == 2:                       # no browser, or Node.js older than 22
            self.skipTest(r.stdout.strip())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
