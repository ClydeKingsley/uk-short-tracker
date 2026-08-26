from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from short_tracker.db import Database
from short_tracker.fca import FCADataService, FCA_SOURCES
from short_tracker.public_export import PublicExportError, export_public_data
from tests.test_fca_data import FixtureOpener, fixture_payloads, xlsx_bytes


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_dir = self.root / "source"
        self.database_path = self.source_dir / "short-tracker.sqlite3"
        self.db = Database(self.database_path)
        self.payloads = fixture_payloads()
        self.service = FCADataService(
            self.db,
            self.source_dir / "snapshots",
            opener=FixtureOpener(self.payloads),
            now=lambda: datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _sync(self):
        self.service.sync()

    @staticmethod
    def _tree_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_export_is_deterministic_hashed_and_narrowly_public(self):
        self._sync()
        first = self.root / "public-one"
        second = self.root / "public-two"

        result = export_public_data(self.database_path, first)
        repeated = export_public_data(self.database_path, second)

        self.assertEqual(self._tree_bytes(first), self._tree_bytes(second))
        self.assertEqual(result.manifest_sha256, repeated.manifest_sha256)
        self.assertEqual(result.security_count, 1)
        self.assertEqual(result.ranking_count, 1)
        self.assertEqual(result.file_count, 5)
        expected_paths = {
            "manifest.json",
            "rankings-current.json",
            "securities-index.json",
            "securities/GB0000000001/short-series.json",
            "status.json",
        }
        self.assertEqual(set(self._tree_bytes(first)), expected_paths)

        manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], "ukshort-fca-static-json")
        for item in manifest["files"]:
            body = (first / item["path"]).read_bytes()
            self.assertEqual(item["byte_size"], len(body))
            self.assertEqual(item["sha256"], hashlib.sha256(body).hexdigest())

        all_text = "\n".join(
            body.decode("utf-8") for body in self._tree_bytes(first).values()
        )
        self.assertNotIn(str(self.database_path), all_text)
        self.assertNotIn("Holder A", all_text)
        self.assertNotIn("Holder B", all_text)
        self.assertNotIn("Holder C", all_text)
        for forbidden_key in (
            '"position_holder"',
            '"price"',
            '"ticker"',
            '"yahoo"',
            '"cache"',
            '"database_path"',
            '"archive_path"',
        ):
            self.assertNotIn(forbidden_key, all_text.casefold())

        status = json.loads((first / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["data_mode"], "fca_only")
        self.assertEqual(status["source_authority"], "UK Financial Conduct Authority")
        self.assertTrue(
            all(
                item["source_url"].startswith("https://www.fca.org.uk/")
                for item in status["coverage"].values()
            )
        )

    def test_series_are_exact_isin_grain_even_when_issuer_is_shared(self):
        by_key = {source.key: source for source in FCA_SOURCES}
        self.payloads[by_key["legacy_named_xlsx"].url] = xlsx_bytes(
            [
                [
                    "Position Holder",
                    "Name of Share Issuer",
                    "ISIN",
                    "Net Short Position (%)",
                    "Position Date",
                ],
                ["Sensitive Holder One", "Acme plc", "GB0000000001", 0.60, datetime(2025, 1, 1)],
                ["Sensitive Holder Two", "Acme plc", "GB0000000002", 0.80, datetime(2025, 1, 1)],
            ]
        )
        self.payloads[by_key["ansp_current_csv"].url] = (
            "Name of Company,International Securities Identification Number (ISIN),"
            "Aggregated net short position (%),Position date\n"
            "ACME PLC,GB0000000001,0.90,20/07/2026\n"
            "ACME PLC,GB0000000002,1.10,21/07/2026\n"
        ).encode("utf-8")
        self.payloads[by_key["ansp_historic_csv"].url] = (
            "Name of Company,International Securities Identification Number (ISIN),"
            "Aggregated net short position (%),Position date,"
            "Date the aggregated net short position became historical\n"
            "ACME PLC,GB0000000001,0.75,13/07/2026,20/07/2026\n"
            "ACME PLC,GB0000000002,0.95,13/07/2026,21/07/2026\n"
        ).encode("utf-8")
        self.payloads[by_key["rsl_csv"].url] = (
            "Share ISIN,Company name,Date added,"
            "Class of share (Main or Other Class of Shares)\n"
            "GB0000000001,ACME PLC,13/07/2026,Main\n"
            "GB0000000002,ACME PLC,13/07/2026,Other\n"
        ).encode("utf-8")
        self._sync()
        output = self.root / "public"

        export_public_data(self.database_path, output)

        first = json.loads(
            (output / "securities/GB0000000001/short-series.json").read_text("utf-8")
        )
        second = json.loads(
            (output / "securities/GB0000000002/short-series.json").read_text("utf-8")
        )
        self.assertEqual([row["value_percent"] for row in first["legacy"]], [0.6])
        self.assertEqual([row["value_percent"] for row in second["legacy"]], [0.8])
        self.assertEqual(first["current"]["value_percent"], 0.9)
        self.assertEqual(second["current"]["value_percent"], 1.1)
        self.assertNotEqual(first["security"]["isin"], second["security"]["isin"])

    def test_failed_validation_preserves_previous_complete_export(self):
        self._sync()
        output = self.root / "public"
        export_public_data(self.database_path, output)
        before = self._tree_bytes(output)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE raw_snapshots SET source_url = 'https://example.invalid/not-fca.csv' "
                "WHERE id = (SELECT snapshot_id FROM dataset_heads WHERE dataset_key = 'ansp_current')"
            )

        with self.assertRaisesRegex(PublicExportError, "non-FCA source URL"):
            export_public_data(self.database_path, output)

        self.assertEqual(before, self._tree_bytes(output))
        self.assertFalse(any(output.parent.glob(".public-staging-*")))

    def test_source_data_tree_and_ancestors_cannot_be_export_targets(self):
        self._sync()
        with self.assertRaisesRegex(PublicExportError, "inside the source data"):
            export_public_data(self.database_path, self.source_dir / "public")
        with self.assertRaisesRegex(PublicExportError, "contain the source database"):
            export_public_data(self.database_path, self.root)


if __name__ == "__main__":
    unittest.main()
