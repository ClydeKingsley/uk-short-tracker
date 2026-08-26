from __future__ import annotations

from pathlib import Path
import re
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"


class FrontendInternationalisationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.catalog_source = (WEB_ROOT / "i18n.js").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
        cls.catalog_entries = re.findall(
            r'^\s*"([^"]+)":\s*\["([^"]*)",\s*"([^"]*)"\],?\s*$',
            cls.catalog_source,
            flags=re.MULTILINE,
        )
        cls.catalog = {key: (zh, en) for key, zh, en in cls.catalog_entries}

    def test_catalog_has_unique_non_empty_bilingual_entries(self):
        self.assertGreaterEqual(len(self.catalog), 200)
        self.assertEqual(len(self.catalog_entries), len(self.catalog), "translation keys must be unique")
        for key, variants in self.catalog.items():
            with self.subTest(key=key):
                self.assertTrue(all(value.strip() for value in variants))

    def test_every_html_translation_key_exists(self):
        html_keys = set(
            re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', self.html)
        )
        self.assertGreaterEqual(len(html_keys), 100)
        self.assertEqual(set(), html_keys - self.catalog.keys())

    def test_every_literal_runtime_translation_key_exists(self):
        runtime_keys = set(re.findall(r'\bt\("([^"]+)"', self.app))
        self.assertGreaterEqual(len(runtime_keys), 70)
        self.assertEqual(set(), runtime_keys - self.catalog.keys())

    def test_catalog_loads_before_application_and_switch_is_accessible(self):
        self.assertLess(self.html.index('src="/i18n.js"'), self.html.index('src="/app.js"'))
        self.assertRegex(self.html, r'id="languageSwitch"[^>]+role="group"')
        self.assertIn('data-language="zh-CN"', self.html)
        self.assertIn('data-language="en-GB"', self.html)
        self.assertIn('aria-pressed="true"', self.html)
        self.assertIn('short-tracker-language', self.app)
        self.assertIn('document.documentElement.lang = state.language', self.app)

    def test_primary_kpi_allows_translated_tooltip_to_escape_card(self):
        primary_rule = re.search(r'\.kpi-card-primary\s*\{([^}]+)\}', self.styles)
        self.assertIsNotNone(primary_rule)
        self.assertIn('overflow: visible', primary_rule.group(1))
        self.assertRegex(
            self.styles,
            r'\.kpi-card-primary:hover,\s*\.kpi-card-primary:focus-within\s*\{[^}]*z-index:',
        )
        self.assertRegex(
            self.styles,
            r'\.metric-info::after\s*\{[^}]*overflow-wrap:\s*anywhere',
        )

    def test_automation_and_update_controls_are_bilingual_and_accessible(self):
        for element_id in (
            "settingsButton",
            "settingsDialog",
            "autoSyncEnabled",
            "autoSyncInterval",
            "checkUpdateButton",
            "updateBanner",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
        self.assertRegex(self.html, r'id="settingsDialog"[^>]+aria-labelledby="settingsDialogTitle"')
        self.assertIn('PUT /api/settings', self.app)
        self.assertIn('POST /api/update/check', self.app)
        self.assertIn('url.protocol !== "https:" || url.hostname !== "github.com"', self.app)
        self.assertIn('STATUS_POLL_INTERVAL = 30_000', self.app)


if __name__ == "__main__":
    unittest.main()
