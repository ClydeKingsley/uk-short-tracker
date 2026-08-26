from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import hashlib
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from short_tracker.db import Database
from short_tracker.fca import (
    FCADataError,
    FCADataService,
    FCA_SOURCES,
    MAX_CURRENT_RANKING_LIMIT,
)


def xlsx_bytes(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class FakeResponse:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.status = 200
        self.headers = {
            "Content-Type": content_type,
            "Last-Modified": "Fri, 21 Aug 2026 10:55:52 GMT",
            "ETag": '"fixture"',
        }

    def read(self):
        return self._body

    def close(self):
        pass


class FixtureOpener:
    def __init__(self, payloads):
        self.payloads = payloads
        self.requests = []

    def __call__(self, request, timeout=0):
        self.requests.append((request, timeout))
        body = self.payloads[request.full_url]
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if request.full_url.endswith(".xlsx")
            else "text/csv"
        )
        return FakeResponse(body, content_type)


def fixture_payloads(current_value="0.90"):
    by_key = {source.key: source for source in FCA_SOURCES}
    legacy = xlsx_bytes(
        [
            [
                "Position Holder",
                "Name of Share Issuer",
                "ISIN",
                "Net Short Position (%)",
                "Position Date",
            ],
            ["Holder A", "Acme plc", "GB0000000001", 0.60, datetime(2025, 1, 1)],
            ["Holder B", "Acme plc", "GB0000000001", 0.70, datetime(2025, 1, 1)],
            # A below-threshold disclosure removes Holder A from the public state.
            ["Holder A", "Acme plc", "GB0000000001", 0.40, datetime(2025, 1, 2)],
            # Conflicting same-day rows: the later FCA source row is authoritative.
            ["Holder B", "Acme plc", "GB0000000001", 0.80, datetime(2025, 1, 3)],
            ["Holder B", "Acme plc", "GB0000000001", 0.00, datetime(2025, 1, 3)],
            # One exact duplicate is retained as provenance but has no double effect.
            ["Holder C", "Acme plc", "GB0000000001", 0.55, datetime(2025, 1, 4)],
            ["Holder C", "Acme plc", "GB0000000001", 0.55, datetime(2025, 1, 4)],
            ["Holder A", "Acme plc", "GB0000000001", 0.50, datetime(2025, 1, 4)],
        ]
    )
    current = (
        "Name of Company,International Securities Identification Number (ISIN),"
        "Aggregated net short position (%),Position date\n"
        f"ACME PLC,GB0000000001,{current_value},20/07/2026\n"
    ).encode("utf-8")
    historic = (
        "Name of Company,International Securities Identification Number (ISIN),"
        "Aggregated net short position (%),Position date,"
        "Date the aggregated net short position became historical\n"
        "ACME PLC,GB0000000001,0.75,13/07/2026,20/07/2026\n"
    ).encode("utf-8")
    rsl = (
        "Share ISIN,Company name,Date added,Class of share (Main or Other Class of Shares)\n"
        "GB0000000001,ACME PLC,13/07/2026,Main\n"
    ).encode("utf-8")
    combined = xlsx_bytes([["Current ANSP"], ["fixture"]])
    rsl_xlsx = xlsx_bytes(
        [["Share ISIN", "Company name", "Date added", "Class of share"],
         ["GB0000000001", "ACME PLC", "13/07/2026", "Main"]]
    )
    return {
        by_key["legacy_named_xlsx"].url: legacy,
        by_key["ansp_current_csv"].url: current,
        by_key["ansp_historic_csv"].url: historic,
        by_key["ansp_combined_xlsx"].url: combined,
        by_key["rsl_csv"].url: rsl,
        by_key["rsl_xlsx"].url: rsl_xlsx,
    }


class FCADataServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = Database(self.root / "short-tracker.sqlite3")
        self.payloads = fixture_payloads()
        self.opener = FixtureOpener(self.payloads)
        self.service = FCADataService(
            self.db,
            self.root / "data",
            opener=self.opener,
            now=lambda: datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_sync_archives_every_official_format_with_hash_provenance(self):
        result = self.service.sync()

        self.assertEqual("success", result["status"])
        self.assertEqual(6, len(result["sources"]))
        self.assertEqual(
            {
                "legacy_named_xlsx",
                "ansp_current_csv",
                "ansp_historic_csv",
                "ansp_combined_xlsx",
                "rsl_csv",
                "rsl_xlsx",
            },
            set(result["sources"]),
        )
        for source_result in result["sources"].values():
            path = self.root / "data" / source_result["archive_path"]
            self.assertTrue(path.is_file())
            self.assertEqual(source_result["file_name"], path.name)
            self.assertEqual(
                source_result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
            )
            self.assertEqual("2026-08-21", source_result["effective_date"])

        self.assertEqual(8, result["imports"]["legacy_named"]["row_count"])
        profile = result["imports"]["legacy_named"]["profile"]
        self.assertEqual(1, profile["exact_duplicate_row_count"])
        self.assertEqual(2, profile["duplicate_composite_key_count"])
        self.assertEqual(1, profile["conflicting_duplicate_key_count"])
        self.assertIn("last_source_row_wins", profile["duplicate_resolution"])

    def test_legacy_replay_threshold_closures_and_last_row_wins(self):
        self.service.sync()
        security = self.service.search("GB0000000001")[0]
        series = self.service.get_short_series(security["id"])
        self.assertIsNotNone(series)

        legacy = series["legacy"]
        self.assertEqual(
            [
                ("2025-01-01", 1.30, 2),
                ("2025-01-02", 0.70, 1),
                ("2025-01-03", 0.00, 0),
                ("2025-01-04", 1.05, 2),
            ],
            [
                (row["date"], row["value"], row["active_disclosed_holders"])
                for row in legacy
            ],
        )
        self.assertEqual(2, legacy[2]["source_event_count"])
        self.assertEqual(3, legacy[3]["source_event_count"])

        # The official anonymous series remains separate rather than being
        # spliced onto the reconstructed named series.
        self.assertEqual(2, len(series["ansp"]))
        self.assertEqual(0.75, series["ansp"][0]["value"])
        self.assertEqual(0.90, series["current"]["value"])
        self.assertEqual("anonymous_fca_ansp", series["current"]["regime"])
        self.assertEqual("ACME PLC", series["security"]["name"])
        self.assertTrue(series["security"]["reportable"])

    def test_sync_is_idempotent_and_failed_revision_does_not_move_heads(self):
        first = self.service.sync()
        second = self.service.sync()
        self.assertFalse(second["changed"])
        self.assertTrue(all(item["reused"] for item in second["imports"].values()))
        self.assertEqual(6, self.db.query_one("SELECT COUNT(*) AS n FROM raw_snapshots")["n"])
        self.assertEqual(8, self.db.query_one("SELECT COUNT(*) AS n FROM legacy_events")["n"])

        # A valid revision creates a new immutable snapshot and becomes active.
        revised = fixture_payloads(current_value="0.95")
        self.opener.payloads.update(revised)
        third = self.service.sync()
        self.assertEqual("downloaded", third["sources"]["ansp_current_csv"]["status"])
        self.assertEqual(0.95, self.service.get_short_series("GB0000000001")["current"]["value"])

        active_before_failure = self.db.query_one(
            "SELECT snapshot_id FROM dataset_heads WHERE dataset_key = 'ansp_current'"
        )["snapshot_id"]

        # The current file changes again, but a malformed historic file makes
        # the overall sync fail.  The last known-good heads must remain intact.
        failed_payloads = fixture_payloads(current_value="1.10")
        historic_url = next(
            source.url for source in FCA_SOURCES if source.key == "ansp_historic_csv"
        )
        failed_payloads[historic_url] = b"wrong,columns\n1,2\n"
        self.opener.payloads.update(failed_payloads)
        with self.assertRaises(FCADataError):
            self.service.sync()
        active_after_failure = self.db.query_one(
            "SELECT snapshot_id FROM dataset_heads WHERE dataset_key = 'ansp_current'"
        )["snapshot_id"]
        self.assertEqual(active_before_failure, active_after_failure)
        self.assertEqual(0.95, self.service.get_short_series("GB0000000001")["current"]["value"])

    def test_current_rankings_preserve_isin_grain_and_stable_ties(self):
        current_url = next(
            source.url for source in FCA_SOURCES if source.key == "ansp_current_csv"
        )
        rsl_url = next(source.url for source in FCA_SOURCES if source.key == "rsl_csv")
        self.opener.payloads[current_url] = (
            "Name of Company,International Securities Identification Number (ISIN),"
            "Aggregated net short position (%),Position date\n"
            "ZETA PLC,GB0000000005,1.50,18/08/2026\n"
            "ALPHA PLC,GB0000000002,1.00,17/08/2026\n"
            "BETA PLC,GB0000000004,1.00,16/08/2026\n"
            "ALPHA PLC,GB0000000001,1.00,15/08/2026\n"
            "BETA PLC,GB0000000003,1.50,14/08/2026\n"
        ).encode("utf-8")
        self.opener.payloads[rsl_url] = (
            "Share ISIN,Company name,Date added,"
            "Class of share (Main or Other Class of Shares)\n"
            "GB0000000001,ALPHA PLC,13/07/2026,Main\n"
            "GB0000000002,ALPHA PLC,13/07/2026,Other\n"
            "GB0000000003,BETA PLC,13/07/2026,Main\n"
            "GB0000000004,BETA PLC,13/07/2026,Other\n"
            "GB0000000005,ZETA PLC,13/07/2026,Main\n"
        ).encode("utf-8")
        self.service.sync()

        payload = self.service.get_current_rankings()

        self.assertEqual(payload["total_count"], 5)
        self.assertEqual(payload["total"], 5)
        self.assertEqual(payload["as_of_date"], "2026-08-18")
        self.assertEqual(payload["count"], 5)
        self.assertFalse(payload["truncated"])
        self.assertEqual(
            [
                (row["rank"], row["company"], row["isin"], row["aggregate_bp"])
                for row in payload["items"]
            ],
            [
                (1, "BETA PLC", "GB0000000003", 150),
                (2, "ZETA PLC", "GB0000000005", 150),
                (3, "ALPHA PLC", "GB0000000001", 100),
                (4, "ALPHA PLC", "GB0000000002", 100),
                (5, "BETA PLC", "GB0000000004", 100),
            ],
        )
        # Two BETA share classes resolve to one issuer but remain two ranking
        # rows; 1.50% and 1.00% must never become a fabricated 2.50% issuer sum.
        beta = [row for row in payload["items"] if row["company"] == "BETA PLC"]
        self.assertEqual(len({row["issuer_id"] for row in beta}), 1)
        self.assertEqual([row["percent"] for row in beta], [1.5, 1.0])
        self.assertEqual([row["source_row_number"] for row in beta], [6, 4])
        self.assertEqual([row["row_number"] for row in beta], [6, 4])
        self.assertEqual([row["short_percent"] for row in beta], [1.5, 1.0])
        self.assertEqual([row["security_id"] for row in beta], [beta[0]["issuer_id"]] * 2)
        self.assertEqual(payload["coverage"]["row_count"], 5)
        self.assertEqual(payload["source"]["source_key"], "ansp_current_csv")
        self.assertIn("SSR 6.3.1", payload["methodology"]["position_date"])

        limited = self.service.get_current_rankings(limit=3)
        self.assertEqual(limited["count"], 3)
        self.assertEqual(limited["total_count"], 5)
        self.assertTrue(limited["truncated"])
        self.assertEqual([row["rank"] for row in limited["items"]], [1, 2, 3])

    def test_current_rankings_limit_validation_and_empty_state(self):
        empty = self.service.get_current_rankings(limit=10)
        self.assertEqual(empty["items"], [])
        self.assertIsNone(empty["coverage"])
        self.assertEqual(self.service.get_current_rankings(limit=0)["limit"], 1)
        self.assertEqual(
            self.service.get_current_rankings(limit=MAX_CURRENT_RANKING_LIMIT + 1)["limit"],
            MAX_CURRENT_RANKING_LIMIT,
        )
        with self.assertRaises(TypeError):
            self.service.get_current_rankings(limit=True)


if __name__ == "__main__":
    unittest.main()
