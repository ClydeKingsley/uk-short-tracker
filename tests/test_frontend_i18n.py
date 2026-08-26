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

    def test_ansp_interval_metadata_survives_frontend_normalization(self):
        self.assertIn('const rawItems = forced.length ? [...direct, ...forced] : direct;', self.app)
        self.assertIn('"interval_end"', self.app)
        self.assertIn('"is_current"', self.app)
        self.assertIn('existing.anspIntervalEnd = intervalEnd;', self.app)
        self.assertIn('existing.anspIsCurrent = isCurrent;', self.app)
        self.assertRegex(
            self.app,
            r'if \(isCurrent === true\) \{\s*existing\.anspIntervalEnd = null;',
        )

    def test_ansp_effective_axis_is_not_clamped_to_publication_marker(self):
        self.assertIn(
            'this.drawStepSeries("ansp", this.colors.ansp, shortBounds, upper);',
            self.app,
        )
        self.assertNotIn(
            'Math.max(this.viewStart, REGIME_SWITCH)',
            self.app,
        )
        self.assertIn(
            'const ansp = valueAtOrBefore(this.shortSeries, time, "ansp");',
            self.app,
        )
        self.assertIn('const x = this.xForTime(REGIME_SWITCH);', self.app)

    def test_ansp_historic_interval_ends_in_a_gap_while_current_is_open(self):
        self.assertIn('function buildShortIntervals(items, field, effectiveEnd', self.app)
        self.assertIn('if (isCurrent === true) intervalEnd = Infinity;', self.app)
        self.assertIn(
            'else if (Number.isFinite(point.anspIntervalEnd)) intervalEnd = Math.min(point.anspIntervalEnd, nextTime);',
            self.app,
        )
        self.assertIn(
            'else if (!Number.isFinite(nextTime) && isCurrent === false) intervalEnd = point.time;',
            self.app,
        )
        self.assertIn(
            'if (interval.time <= target && target < interval.intervalEnd) return interval;',
            self.app,
        )
        self.assertIn(
            'interval.intervalEnd > this.viewStart && interval.time <= this.viewEnd',
            self.app,
        )
        self.assertIn(
            '.filter((interval) => interval.visibleEnd > interval.visibleStart);',
            self.app,
        )

    def test_active_ansp_tooltip_exposes_compact_bilingual_audit_metadata(self):
        for element_id in (
            "tooltipAnspAudit",
            "tooltipAnspEffective",
            "tooltipAnspPositionDate",
            "tooltipAnspIntervalEnd",
            "tooltipAnspDateBasis",
            "tooltipAnspFirstPublished",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
                self.assertIn(f'"{element_id}"', self.app)
        for key in (
            "chart.auditTitle",
            "chart.auditEffectiveFrom",
            "chart.auditPositionDate",
            "chart.auditIntervalEnd",
            "chart.auditDateBasis",
            "chart.auditFirstPublished",
            "chart.auditCurrentOpen",
            "chart.auditBasisInitial",
            "chart.auditBasisPreviousHistoric",
        ):
            with self.subTest(key=key):
                self.assertIn(key, self.catalog)
        for normalized_field in (
            "anspPositionTime",
            "anspChartDateBasis",
            "anspFirstPublishedTime",
        ):
            with self.subTest(normalized_field=normalized_field):
                self.assertIn(normalized_field, self.app)
        self.assertIn('anspInterval: ansp,', self.app)
        self.assertIn('dom.tooltipAnspAudit.hidden = !values.anspInterval;', self.app)
        self.assertIn(
            'initial_ansp_scope_and_constituent_position_date: "chart.auditBasisInitial"',
            self.app,
        )
        self.assertIn(
            'previous_became_historical_date: "chart.auditBasisPreviousHistoric"',
            self.app,
        )

    def test_method_copy_separates_ansp_effective_axis_from_first_publication(self):
        chinese, english = self.catalog["method.bodyTwo"]
        self.assertIn("2026-07-09", chinese)
        self.assertIn("2026-07-13", chinese)
        self.assertIn("首次发布日期", chinese)
        self.assertIn("并非所有 ANSP 区间统一的生效日", chinese)
        self.assertNotIn("旧制实名公开披露截至 2026-07-10", chinese)
        self.assertIn("9 July 2026", english)
        self.assertIn("13 July 2026", english)
        self.assertIn("first ANSP publication date", english)
        self.assertIn("not a universal effective date", english)
        self.assertNotIn("run through 10 July 2026", english)


if __name__ == "__main__":
    unittest.main()
