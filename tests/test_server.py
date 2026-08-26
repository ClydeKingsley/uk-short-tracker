from __future__ import annotations

import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr

from short_tracker.__main__ import _build_parser, _verification_report
from short_tracker.cache import PriceCache
from short_tracker.db import Database
from short_tracker.fca import FCA_SOURCES
from short_tracker.process_lock import ProcessFileLock
from short_tracker.prices import PriceAdapterError
from short_tracker.scheduler import AutoSyncScheduler
from short_tracker.server import (
    APIError,
    ShortTrackerApplication,
    ShortTrackerHTTPServer,
    SyncCoordinator,
    serve,
)
from short_tracker.settings import SettingsStore


NOW = "2026-08-24T10:00:00Z"


class DictResult:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return dict(self.payload)


class FakeUpdateChecker:
    def __init__(self):
        self.calls = []

    def status(self, *, current_version):
        self.calls.append(("status", current_version, None))
        return DictResult(
            {
                "enabled": False,
                "status": "disabled",
                "channel": "stable",
                "current_version": current_version,
                "source": "disabled",
                "cache_fresh": False,
                "checked_at_utc": None,
                "next_check_at_utc": None,
                "retry_at_utc": None,
                "error_code": None,
                "release": None,
            }
        )

    def check(self, *, current_version, force=False):
        self.calls.append(("check", current_version, force))
        return self.status(current_version=current_version)


class FakePriceProvider:
    def __init__(self):
        self.history_calls = 0
        self.latest_calls = 0
        self.suggestion_calls = []
        self.suggestions = ()
        self.suggestion_error = None

    def get_daily_history(self, symbol):
        self.history_calls += 1
        return DictResult(
            {
                "symbol": symbol,
                "display_name": "Example plc",
                "currency": "GBp",
                "bars": [
                    {
                        "date": "2026-08-21",
                        "open": 99.0,
                        "high": 102.0,
                        "low": 98.0,
                        "close": 101.0,
                        "adjusted_close": 101.0,
                        "volume": 1000,
                    }
                ],
                "fetched_at_utc": "2026-08-24T09:59:00Z",
            }
        )

    def get_latest_price(self, symbol):
        self.latest_calls += 1
        return DictResult(
            {
                "symbol": symbol,
                "display_name": "Example plc",
                "currency": "GBp",
                "price": 103.0,
                "as_of_utc": "2026-08-24T09:58:00Z",
                "fetched_at_utc": "2026-08-24T10:00:00Z",
            }
        )

    def resolve_uk_symbol(self, *_args, **_kwargs):
        raise AssertionError("a saved mapping should avoid automatic network resolution")

    def suggest_uk_symbols(self, query, **kwargs):
        self.suggestion_calls.append((query, kwargs))
        if self.suggestion_error:
            raise self.suggestion_error
        return self.suggestions


class FakeFCA:
    def __init__(self, *, block_sync=False):
        self.block_sync = block_sync
        self.sync_started = threading.Event()
        self.sync_release = threading.Event()
        self.sync_calls = 0
        self.forces = []
        self.security = {
            "id": 7,
            "name": "Example plc",
            "normalized_name": "example plc",
            "primary_isin": "GB0000000007",
            "isins": ["GB0000000007"],
            "aliases": ["Example plc"],
            "reportable": True,
            "reportable_shares": [],
            "current_ansp": {
                "value": 1.23,
                "position_date": "2026-08-22",
            },
        }

    def sync(self, *, force=False):
        self.sync_calls += 1
        self.forces.append(force)
        self.sync_started.set()
        if self.block_sync and not self.sync_release.wait(5):
            raise TimeoutError("test did not release fake sync")
        return {"changed": True, "force": force}

    def search(self, query, *, limit=20):
        if query and query.casefold() not in self.security["name"].casefold():
            return []
        return [{**self.security}][:limit]

    def get_security(self, identifier):
        if str(identifier) in {"7", self.security["primary_isin"]}:
            return {**self.security}
        return None

    def get_short_series(self, identifier):
        if self.get_security(identifier) is None:
            return None
        return {
            "security": {**self.security},
            "legacy": [
                {"date": "2026-07-10", "value": 0.75, "regime": "legacy"}
            ],
            "ansp": [
                {"date": "2026-08-22", "value": 1.23, "regime": "ansp"}
            ],
            "current": {"date": "2026-08-22", "value": 1.23},
            "coverage": {
                "legacy_named": {"last_checked_at": "2026-08-23T08:00:00Z"},
                "ansp_current": {"last_checked_at": "2026-08-24T09:00:00Z"},
                "ansp_historic": None,
            },
            "methodology": {"unit": "percentage points"},
        }

    def get_current_rankings(self, limit=2000):
        items = [
            {
                "rank": 1,
                "security_id": 8,
                "name": "Alpha plc",
                "isin": "GB0000000008",
                "aggregate_bp": 234,
                "short_percent": 2.34,
                "position_date": "2026-08-21",
                "row_number": 2,
            },
            {
                "rank": 2,
                "security_id": 7,
                "name": "Example plc",
                "isin": "GB0000000007",
                "aggregate_bp": 123,
                "short_percent": 1.23,
                "position_date": "2026-08-22",
                "row_number": 1,
            },
        ][:limit]
        return {
            "items": items,
            "total": 2,
            "as_of_date": "2026-08-22",
            "coverage": {"last_checked_at": "2026-08-24T09:00:00Z"},
            "methodology": {"grain": "reportable_share_isin"},
        }


def make_application(root: Path, *, block_sync=False):
    data_dir = root / "data"
    web_dir = root / "web"
    data_dir.mkdir(parents=True, exist_ok=True)
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "index.html").write_text("<!doctype html><title>Short Tracker</title>", encoding="utf-8")
    (web_dir / "styles.css").write_text("body{}", encoding="utf-8")
    (web_dir / "i18n.js").write_text("window.SHORT_TRACKER_COPY = {};", encoding="utf-8")
    (web_dir / "app.js").write_text("'use strict';", encoding="utf-8")
    db = Database(data_dir / "test.sqlite")
    fca = FakeFCA(block_sync=block_sync)
    prices = FakePriceProvider()
    coordinator = SyncCoordinator(
        fca,
        process_lock=ProcessFileLock(data_dir / "runtime" / "fca-sync.lock"),
    )
    settings = SettingsStore(data_dir)
    settings.load()
    app = ShortTrackerApplication(
        root_dir=root,
        data_dir=data_dir,
        web_dir=web_dir,
        db=db,
        fca=fca,
        prices=prices,
        price_cache=PriceCache(data_dir),
        sync=coordinator,
        settings=settings,
    )
    app.auto_sync = AutoSyncScheduler(settings, coordinator, db, app.has_data)
    app.update_checker = FakeUpdateChecker()
    return app, fca, prices


class SyncCoordinatorTests(unittest.TestCase):
    def test_start_reserves_before_worker_and_rejects_second_start(self):
        service = FakeFCA(block_sync=True)
        coordinator = SyncCoordinator(service)
        self.assertTrue(coordinator.start(force=True))
        self.assertTrue(service.sync_started.wait(1))
        self.assertFalse(coordinator.start(force=False))
        self.assertTrue(coordinator.snapshot()["running"])

        service.sync_release.set()
        deadline = time.monotonic() + 2
        while coordinator.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertFalse(coordinator.snapshot()["running"])
        self.assertEqual(service.sync_calls, 1)
        self.assertEqual(service.forces, [True])


class ApplicationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app, self.fca, self.prices = make_application(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_short_series_has_frontend_freshness_aliases(self):
        payload = self.app.get_short_series("7")
        self.assertEqual(payload["latest_date"], "2026-08-22")
        self.assertEqual(payload["fetched_at"], "2026-08-24T09:00:00Z")
        self.assertEqual(payload["fetched_at_utc"], payload["fetched_at"])
        self.assertEqual([item["date"] for item in payload["items"]], ["2026-07-10", "2026-08-22"])

    def test_security_search_matches_saved_ticker_exact_and_prefix_locally(self):
        self.app.price_cache.set_symbol_mapping(
            7,
            symbol="EXM.L",
            source="user_reviewed",
            display_name="Example plc",
        )
        prefix = self.app.search_securities("EXM", 20)
        exact = self.app.search_securities("exm.l", 20)

        self.assertEqual([item["id"] for item in prefix["items"]], [7])
        self.assertEqual(prefix["items"][0]["ticker"], "EXM.L")
        self.assertEqual(prefix["items"][0]["ticker_source"], "local_mapping")
        self.assertEqual(exact["items"][0]["ticker"], "EXM.L")
        self.assertEqual(self.prices.suggestion_calls, [])

    def test_ticker_fallback_maps_provider_name_without_persisting(self):
        self.fca.security["name"] = "Ocado Group plc"
        self.fca.security["normalized_name"] = "ocado group plc"
        self.prices.suggestions = (
            SimpleNamespace(symbol="OCDO.L", display_name="Ocado Group plc"),
        )

        payload = self.app.search_securities("OCDO", 20)

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["id"], 7)
        self.assertEqual(payload["items"][0]["ticker"], "OCDO.L")
        self.assertEqual(
            payload["items"][0]["ticker_source"], "price_provider_suggestion"
        )
        self.assertEqual(len(self.prices.suggestion_calls), 1)
        self.assertIsNone(self.app.price_cache.get_symbol_mapping(7))

        # An ordinary company-name query remains fully local.
        self.app.search_securities("Ocado", 20)
        self.assertEqual(len(self.prices.suggestion_calls), 1)

    def test_ticker_fallback_failure_does_not_break_local_search_api(self):
        self.prices.suggestion_error = PriceAdapterError("offline")
        payload = self.app.search_securities("WXYZ", 20)
        self.assertEqual(payload["items"], [])
        self.assertEqual(len(payload["warnings"]), 1)

    def test_prices_have_aliases_and_saving_mapping_returns_full_security(self):
        saved = self.app.save_price_symbol(
            "7", {"symbol": " exm.l ", "display_name": "Example plc"}
        )
        self.assertEqual(saved["symbol"], "EXM.L")
        self.assertEqual(saved["security"]["name"], "Example plc")
        self.assertEqual(saved["security"]["price_symbol"], "EXM.L")
        self.assertEqual(saved["security"]["primary_isin"], "GB0000000007")
        self.assertEqual(saved["security"]["price_mapping"]["source"], "user_reviewed")

        payload = self.app.get_prices("7", refresh=True)
        self.assertEqual(payload["latest_date"], "2026-08-24")
        self.assertEqual(payload["fetched_at"], "2026-08-24T10:00:00Z")
        self.assertEqual(payload["fetched_at_utc"], payload["fetched_at"])
        self.assertEqual(payload["items"][-1]["close"], 103.0)
        self.assertEqual(self.prices.history_calls, 1)
        self.assertEqual(self.prices.latest_calls, 1)

    def test_current_rankings_are_enriched_filtered_sorted_and_paginated(self):
        self.app.price_cache.set_symbol_mapping(
            7,
            symbol="EXM.L",
            source="user_reviewed",
            display_name="Example plc",
        )

        ticker_match = self.app.get_current_rankings(query="exm", page_size=25)
        self.assertEqual(ticker_match["total"], 1)
        self.assertEqual(ticker_match["items"][0]["security_id"], 7)
        self.assertEqual(ticker_match["items"][0]["ticker"], "EXM.L")
        self.assertEqual(
            ticker_match["items"][0]["ticker_provenance"]["source"],
            "user_reviewed",
        )
        self.assertEqual(ticker_match["items"][0]["short_percent"], 1.23)

        ascending = self.app.get_current_rankings(
            sort="short_percent", order="asc", page=2, page_size=1
        )
        self.assertEqual(ascending["total_pages"], 2)
        self.assertEqual(ascending["items"][0]["name"], "Alpha plc")
        self.assertEqual(ascending["items"][0]["rank"], 1)

        name_desc = self.app.get_current_rankings(sort="name", order="desc", page_size=2000)
        self.assertEqual([item["name"] for item in name_desc["items"]], ["Example plc", "Alpha plc"])
        date_asc = self.app.get_current_rankings(
            sort="position_date", order="asc", page_size=2000
        )
        self.assertEqual([item["rank"] for item in date_asc["items"]], [1, 2])
        beyond = self.app.get_current_rankings(page=3, page_size=1)
        self.assertEqual(beyond["items"], [])
        self.assertEqual(beyond["total_pages"], 2)

    def test_current_ranking_default_sort_preserves_fca_tie_order(self):
        self.fca.get_current_rankings = lambda limit=2000: {
            "items": [
                {
                    "rank": 1,
                    "security_id": 7,
                    "name": "Tie plc",
                    "isin": "GB0000000009",
                    "aggregate_bp": 150,
                    "short_percent": 1.5,
                    "position_date": "2026-08-22",
                    "row_number": 9,
                },
                {
                    "rank": 2,
                    "security_id": 8,
                    "name": "tie plc",
                    "isin": "GB0000000001",
                    "aggregate_bp": 150,
                    "short_percent": 1.5,
                    "position_date": "2026-08-22",
                    "row_number": 1,
                },
            ],
            "total": 2,
            "as_of_date": "2026-08-22",
            "coverage": {},
            "methodology": {},
        }

        payload = self.app.get_current_rankings(page_size=25)

        self.assertEqual([item["rank"] for item in payload["items"]], [1, 2])
        self.assertEqual(
            [item["isin"] for item in payload["items"]],
            ["GB0000000009", "GB0000000001"],
        )

    def test_multi_isin_rows_do_not_reuse_issuer_ticker_and_expose_truncation(self):
        self.app.price_cache.set_symbol_mapping(
            7,
            symbol="ONE.L",
            source="user_reviewed",
            display_name="One share class",
        )
        self.fca.get_current_rankings = lambda limit=2000: {
            "items": [
                {
                    "rank": rank,
                    "security_id": 7,
                    "name": "Multi Class plc",
                    "isin": isin,
                    "aggregate_bp": value,
                    "short_percent": value / 100,
                    "position_date": "2026-08-22",
                    "row_number": rank,
                }
                for rank, isin, value in (
                    (1, "GB0000000001", 200),
                    (2, "GB0000000002", 150),
                )
            ],
            "total": 2001,
            "limit": 2000,
            "truncated": True,
            "as_of_date": "2026-08-22",
            "coverage": {},
            "methodology": {},
        }

        payload = self.app.get_current_rankings(page_size=2000)

        self.assertEqual([item["ticker"] for item in payload["items"]], [None, None])
        self.assertTrue(payload["source_truncated"])
        self.assertEqual(payload["source_total"], 2001)
        self.assertEqual(payload["source_limit"], 2000)
        self.assertEqual(payload["total"], 2)

    def test_invalid_and_future_position_dates_are_not_presented_as_today(self):
        self.fca.get_current_rankings = lambda limit=2000: {
            "items": [
                {
                    "rank": 1,
                    "security_id": 7,
                    "name": "Future plc",
                    "isin": "GB0000000001",
                    "aggregate_bp": 200,
                    "short_percent": 2.0,
                    "position_date": "2999-01-01",
                    "row_number": 1,
                },
                {
                    "rank": 2,
                    "security_id": 8,
                    "name": "Invalid plc",
                    "isin": "GB0000000002",
                    "aggregate_bp": 100,
                    "short_percent": 1.0,
                    "position_date": "not-a-date",
                    "row_number": 2,
                },
            ],
            "total": 2,
            "as_of_date": "2999-01-01",
            "coverage": {},
            "methodology": {},
        }

        payload = self.app.get_current_rankings(page_size=25)

        self.assertEqual(payload["age_reference_timezone"], "UTC")
        self.assertIsNone(payload["items"][0]["position_age_days"])
        self.assertTrue(payload["items"][0]["position_date_in_future"])
        self.assertIsNone(payload["items"][1]["position_age_days"])
        self.assertFalse(payload["items"][1]["position_date_in_future"])


class HTTPServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app, self.fca, _ = make_application(self.root, block_sync=True)
        (self.root / "secret.txt").write_text("must not leak", encoding="utf-8")
        self.server = ShortTrackerHTTPServer(("127.0.0.1", 0), self.app)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.fca.sync_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def test_shutdown_watcher_rechecks_sync_and_rejects_racing_normal_stop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            guarded_port = int(probe.getsockname()[1])

        service = FakeFCA(block_sync=True)
        coordinator = SyncCoordinator(service)
        self.assertTrue(coordinator.start(force=False))
        self.assertTrue(service.sync_started.wait(1))
        guarded_app = SimpleNamespace(
            sync=coordinator,
            begin_closing=lambda *, require_idle: coordinator.begin_closing(
                require_idle=require_idle
            ),
            drain=lambda: coordinator.wait_for_idle(timeout=None),
        )
        stop_file = self.root / "guarded-stop.request"
        stop_file.write_text(json.dumps({"schema": 1, "force": False}), encoding="utf-8")
        errors: list[Exception] = []

        def worker():
            try:
                serve(
                    guarded_app,
                    "127.0.0.1",
                    guarded_port,
                    shutdown_file=stop_file,
                    start_auto_sync=False,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        guarded_thread = threading.Thread(target=worker, daemon=True)
        guarded_thread.start()
        deadline = time.monotonic() + 3
        while stop_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(stop_file.exists(), "normal stop request was not rejected")
        self.assertTrue(guarded_thread.is_alive(), "server stopped during an active sync")
        self.assertFalse(coordinator.snapshot()["closing"])

        service.sync_release.set()
        self.assertTrue(coordinator.wait_for_idle(timeout=2))
        stop_file.write_text(json.dumps({"schema": 1, "force": False}), encoding="utf-8")
        guarded_thread.join(3)
        if guarded_thread.is_alive():
            stop_file.write_text(json.dumps({"schema": 1, "force": True}), encoding="utf-8")
            guarded_thread.join(2)
        self.assertFalse(guarded_thread.is_alive())
        self.assertEqual(errors, [])

    def test_explicit_force_shutdown_bypasses_service_sync_guard(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            guarded_port = int(probe.getsockname()[1])

        service = FakeFCA(block_sync=True)
        coordinator = SyncCoordinator(service)
        self.assertTrue(coordinator.start(force=False))
        self.assertTrue(service.sync_started.wait(1))
        guarded_app = SimpleNamespace(
            sync=coordinator,
            begin_closing=lambda *, require_idle: coordinator.begin_closing(
                require_idle=require_idle
            ),
            drain=lambda: coordinator.wait_for_idle(timeout=None),
        )
        stop_file = self.root / "forced-stop.request"
        stop_file.write_text(json.dumps({"schema": 1, "force": True}), encoding="utf-8")
        errors: list[Exception] = []

        def worker():
            try:
                serve(
                    guarded_app,
                    "127.0.0.1",
                    guarded_port,
                    shutdown_file=stop_file,
                    start_auto_sync=False,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        guarded_thread = threading.Thread(target=worker, daemon=True)
        guarded_thread.start()
        deadline = time.monotonic() + 2
        while not coordinator.snapshot()["closing"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(coordinator.snapshot()["closing"])
        self.assertTrue(guarded_thread.is_alive(), "force stop interrupted an active writer")
        with self.assertRaises(APIError) as captured:
            coordinator.run(force=False)
        self.assertEqual(captured.exception.code, "service_closing")

        service.sync_release.set()
        guarded_thread.join(3)
        self.assertFalse(guarded_thread.is_alive())
        self.assertEqual(errors, [])

    def request(self, method, path, *, payload=None, headers=None):
        request_headers = dict(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
            request_headers.setdefault("Content-Length", str(len(body)))
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if "json" in (response.getheader("Content-Type") or "") else raw
            return response.status, dict(response.getheaders()), parsed
        finally:
            connection.close()

    def test_static_is_confined_and_local_authority_is_enforced(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Short Tracker", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")

        status, headers, body = self.request("GET", "/i18n.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn(b"SHORT_TRACKER_COPY", body)

        status, _, payload = self.request("GET", "/..%5Csecret.txt")
        self.assertEqual(status, 403)
        self.assertIsInstance(payload["message"], str)
        self.assertNotIn("must not leak", json.dumps(payload))

        status, _, payload = self.request(
            "GET", "/api/status", headers={"Host": "attacker.example"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "local_only")

    def test_cross_site_post_is_rejected_before_sync(self):
        status, _, payload = self.request(
            "POST",
            "/api/sync",
            payload={},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "cross_site_request")
        self.assertEqual(self.fca.sync_calls, 0)

        status, _, payload = self.request(
            "PUT",
            "/api/settings",
            payload={"auto_sync": {"enabled": True, "interval_hours": 12}},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "cross_site_request")

    def test_non_json_body_and_excessive_query_fields_are_rejected(self):
        body = b"force=true"
        status, _, payload = self.request(
            "POST",
            "/api/sync",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
            },
        )
        self.assertEqual(status, 415)
        self.assertEqual(payload["error"]["code"], "unsupported_media_type")
        query = "&".join(f"field{index}=1" for index in range(51))
        status, _, payload = self.request("GET", f"/api/securities?{query}")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "too_many_query_fields")

    def test_api_error_contract_status_and_queued_sync(self):
        status, _, payload = self.request("GET", "/api/security/missing")
        self.assertEqual(status, 404)
        self.assertIsInstance(payload["message"], str)
        self.assertEqual(payload["message"], payload["error"]["message"])

        status, _, payload = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "not_ready")

        status, _, payload = self.request("POST", "/api/sync", payload={"force": "false"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_force")
        self.assertEqual(self.fca.sync_calls, 0)

        status, _, payload = self.request("POST", "/api/sync", payload={"force": True})
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")
        self.assertTrue(payload["accepted"])
        self.assertTrue(self.fca.sync_started.wait(1))

        status, _, payload = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "syncing")
        self.assertTrue(payload["sync"]["running"])

        status, _, payload = self.request("POST", "/api/sync", payload={})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "sync_running")

    def test_settings_api_is_strict_persistent_and_updates_scheduler(self):
        status, _, payload = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["auto_sync"],
            {
                "enabled": True,
                "interval_hours": 6,
                "allowed_interval_hours": [6, 12, 24],
            },
        )

        status, _, payload = self.request(
            "PUT",
            "/api/settings",
            payload={"auto_sync": {"enabled": False, "interval_hours": 12}},
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["auto_sync"]["enabled"])
        self.assertEqual(payload["auto_sync"]["interval_hours"], 12)
        self.assertEqual(self.app.auto_sync.snapshot()["interval_hours"], 12)
        saved = json.loads(
            (self.app.data_dir / "settings" / "application.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["auto_sync"]["interval_hours"], 12)

        for invalid in (
            {"auto_sync": {"enabled": True, "interval_hours": 5}},
            {"auto_sync": {"enabled": 1, "interval_hours": 6}},
            {
                "auto_sync": {
                    "enabled": True,
                    "interval_hours": 6,
                    "unexpected": True,
                }
            },
            {"auto_sync": {"enabled": True, "interval_hours": 6}, "extra": 1},
        ):
            status, _, payload = self.request("PUT", "/api/settings", payload=invalid)
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"]["code"], "invalid_settings")

    def test_update_api_is_metadata_only_and_strict(self):
        status, _, payload = self.request("GET", "/api/update/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["status"], "disabled")

        status, _, payload = self.request(
            "POST", "/api/update/check", payload={"force": True}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])
        self.assertIn(("check", payload["current_version"], True), self.app.update_checker.calls)

        for invalid in ({}, {"force": "false"}, {"force": False, "extra": 1}):
            status, _, payload = self.request(
                "POST", "/api/update/check", payload=invalid
            )
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"]["code"], "invalid_update_check")

    def test_status_exposes_auto_sync_contract_and_closing_blocks_mutations(self):
        status, _, payload = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(
            set(payload["auto_sync"]),
            {
                "enabled",
                "interval_hours",
                "running",
                "closing",
                "last_attempt_at",
                "last_success_at",
                "next_check_at",
                "last_error",
                "consecutive_failures",
            },
        )
        self.assertFalse(payload["auto_sync"]["closing"])

        self.assertTrue(self.app.begin_closing(require_idle=True))
        status, _, payload = self.request("POST", "/api/sync", payload={})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "service_closing")
        status, _, payload = self.request(
            "PUT",
            "/api/settings",
            payload={"auto_sync": {"enabled": True, "interval_hours": 24}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "service_closing")

    def test_current_rankings_endpoint_and_parameter_validation(self):
        status, _, payload = self.request(
            "GET", "/api/rankings/current?page=1&page_size=1&sort=short_percent&order=desc"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["items"][0]["name"], "Alpha plc")
        self.assertEqual(payload["items"][0]["short_percent"], 2.34)

        status, _, payload = self.request(
            "GET", "/api/rankings/current?sort=unknown&page_size=2001"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_sort")

        for query, code in (
            ("page=0", "invalid_page"),
            ("page_size=0", "invalid_page_size"),
            ("page_size=2001", "invalid_page_size"),
        ):
            status, _, payload = self.request("GET", f"/api/rankings/current?{query}")
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"]["code"], code)

    def test_ipv6_server_selects_ipv6_socket_family(self):
        try:
            server = ShortTrackerHTTPServer(("::1", 0), self.app)
        except OSError as exc:
            self.skipTest(f"IPv6 loopback unavailable: {exc}")
        try:
            self.assertEqual(server.address_family, socket.AF_INET6)
        finally:
            server.server_close()

    def test_directory_index_symlink_cannot_escape_web_root(self):
        route = self.app.web_dir / "route"
        route.mkdir()
        try:
            (route / "index.html").symlink_to(self.root / "secret.txt")
        except OSError as exc:
            self.skipTest(f"file symlinks unavailable: {exc}")
        status, _, payload = self.request("GET", "/route")
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "path_outside_web_root")


class ServiceLifecycleTests(unittest.TestCase):
    def test_serve_starts_scheduler_and_empty_data_syncs_immediately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app, fca, _ = make_application(root)
            # The fake service does not import dataset heads.  Model successful
            # first-run readiness explicitly so the test scheduler waits six
            # hours after its first check instead of immediately checking again.
            app.auto_sync = AutoSyncScheduler(
                app.settings,
                app.sync,
                app.db,
                lambda: fca.sync_calls >= 1,
                settings_reload_seconds=0.02,
            )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = int(probe.getsockname()[1])
            stop_file = root / "runtime" / "service-stop.request"
            errors: list[Exception] = []

            def worker():
                try:
                    serve(
                        app,
                        "127.0.0.1",
                        port,
                        shutdown_file=stop_file,
                    )
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            self.assertTrue(fca.sync_started.wait(2))
            self.assertTrue(app.sync.wait_for_idle(timeout=2))
            self.assertEqual(fca.forces, [False])
            self.assertIsNotNone(app.status()["auto_sync"]["last_attempt_at"])
            self.assertIsNotNone(app.status()["auto_sync"]["last_success_at"])
            self.assertIsNotNone(app.status()["auto_sync"]["next_check_at"])

            stop_file.parent.mkdir(parents=True, exist_ok=True)
            stop_file.write_text(
                json.dumps({"schema": 1, "force": False}),
                encoding="utf-8",
            )
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_closing_status_is_visible_while_force_stop_drains(self):
        with tempfile.TemporaryDirectory() as temporary:
            app, fca, _ = make_application(Path(temporary), block_sync=True)
            self.assertTrue(app.sync.start(force=False))
            self.assertTrue(fca.sync_started.wait(1))
            self.assertTrue(app.begin_closing(require_idle=False))
            payload = app.status()
            self.assertEqual(payload["status"], "closing")
            self.assertTrue(payload["sync"]["closing"])
            self.assertTrue(payload["auto_sync"]["closing"])
            self.assertTrue(payload["auto_sync"]["running"])

            fca.sync_release.set()
            app.drain()
            self.assertFalse(app.status()["auto_sync"]["running"])


class VerifyTests(unittest.TestCase):
    def test_verify_hashes_archives_and_checks_active_heads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            web_dir = root / "web"
            data_dir.mkdir()
            web_dir.mkdir()
            for name in ("index.html", "styles.css", "i18n.js", "app.js"):
                (web_dir / name).write_text(name, encoding="utf-8")
            db = Database(data_dir / "verify.sqlite")

            archives = {}
            snapshot_ids = {}
            with db.transaction() as conn:
                sync_id = conn.execute(
                    """
                    INSERT INTO sync_runs(started_at, completed_at, status, force)
                    VALUES (?, ?, 'success', 0)
                    """,
                    (NOW, NOW),
                ).lastrowid
                for source in FCA_SOURCES:
                    archive = data_dir / "raw" / source.key / source.file_name
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    content = f"fixture:{source.key}".encode("utf-8")
                    archive.write_bytes(content)
                    archives[source.key] = archive
                    snapshot_id = conn.execute(
                        """
                        INSERT INTO raw_snapshots(
                            source_key, source_url, file_name, sha256, byte_size,
                            content_type, archive_path, first_retrieved_at,
                            last_checked_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
                        """,
                        (
                            source.key,
                            source.url,
                            source.file_name,
                            hashlib.sha256(content).hexdigest(),
                            len(content),
                            "application/octet-stream",
                            str(archive.relative_to(data_dir)),
                            NOW,
                            NOW,
                        ),
                    ).lastrowid
                    snapshot_ids[source.key] = snapshot_id

                active_sources = {
                    "legacy_named": "legacy_named_xlsx",
                    "ansp_current": "ansp_current_csv",
                    "ansp_historic": "ansp_historic_csv",
                    "reportable_shares": "rsl_csv",
                }
                for dataset, source_key in active_sources.items():
                    profile = {"derived_aggregate_row_count": 0} if dataset == "legacy_named" else {}
                    import_id = conn.execute(
                        """
                        INSERT INTO import_runs(
                            dataset_key, snapshot_id, importer_version, started_at,
                            completed_at, status, row_count, profile_json
                        ) VALUES (?, ?, 1, ?, ?, 'success', 0, ?)
                        """,
                        (
                            dataset,
                            snapshot_ids[source_key],
                            NOW,
                            NOW,
                            json.dumps(profile),
                        ),
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO dataset_heads(
                            dataset_key, snapshot_id, import_run_id, sync_run_id, activated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            dataset,
                            snapshot_ids[source_key],
                            import_id,
                            sync_id,
                            NOW,
                        ),
                    )

            app = SimpleNamespace(db=db, data_dir=data_dir, web_dir=web_dir)
            report = _verification_report(app)
            self.assertTrue(report["ok"], report)

            archives["legacy_named_xlsx"].write_bytes(b"tampered")
            report = _verification_report(app)
            self.assertFalse(report["ok"])
            checks = {item["name"]: item for item in report["checks"]}
            self.assertFalse(checks["latest_official_archives"]["passed"])

    def test_cli_rejects_non_loopback_host(self):
        parser = _build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["serve", "--host", "0.0.0.0"])


if __name__ == "__main__":
    unittest.main()
