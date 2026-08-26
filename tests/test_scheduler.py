from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from short_tracker.db import Database
from short_tracker.process_lock import ProcessFileLock
from short_tracker.scheduler import AutoSyncScheduler
from short_tracker.server import APIError, SyncCoordinator
from short_tracker.settings import SettingsError, SettingsStore


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class FakeSyncService:
    def __init__(self, *, failures: int = 0, blocking: bool = False) -> None:
        self.failures = failures
        self.blocking = blocking
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0
        self.forces: list[bool] = []
        self.call_times: list[float] = []
        self.active = 0
        self.maximum_active = 0

    def sync(self, *, force: bool = False):
        with self._lock:
            self.calls += 1
            call_number = self.calls
            self.forces.append(force)
            self.call_times.append(time.monotonic())
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.started.set()
        try:
            if self.blocking and not self.release.wait(3):
                raise TimeoutError("test sync was not released")
            if call_number <= self.failures:
                raise RuntimeError(f"synthetic failure {call_number}")
            return {"ok": True, "call": call_number, "force": force}
        finally:
            with self._lock:
                self.active -= 1


class SchedulerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / "data"
        self.db = Database(self.data_dir / "short_tracker.sqlite")
        self.settings = SettingsStore(self.data_dir)
        self.settings.load()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def coordinator(self, service: FakeSyncService) -> SyncCoordinator:
        return SyncCoordinator(
            service,
            process_lock=ProcessFileLock(self.data_dir / "runtime" / "fca-sync.lock"),
        )

    def scheduler(
        self,
        service: FakeSyncService,
        *,
        has_data,
    ) -> tuple[SyncCoordinator, AutoSyncScheduler]:
        coordinator = self.coordinator(service)
        scheduler = AutoSyncScheduler(
            self.settings,
            coordinator,
            self.db,
            has_data,
            busy_retry_seconds=0.05,
            failure_backoff_base_seconds=0.05,
            failure_backoff_max_seconds=0.2,
            settings_reload_seconds=0.02,
        )
        return coordinator, scheduler

    def test_empty_data_starts_immediately_without_force(self) -> None:
        service = FakeSyncService()
        coordinator, scheduler = self.scheduler(
            service,
            has_data=lambda: service.calls >= 1,
        )
        try:
            self.assertTrue(scheduler.start())
            self.assertTrue(wait_until(lambda: service.calls >= 1))
            self.assertTrue(coordinator.wait_for_idle(timeout=2))
            self.assertEqual(service.forces, [False])
            self.assertTrue(wait_until(lambda: scheduler.snapshot()["last_success_at"] is not None))
            self.assertIsNotNone(scheduler.snapshot()["next_check_at"])
        finally:
            coordinator.begin_closing(require_idle=False)
            scheduler.stop()
            coordinator.wait_for_idle(timeout=2)

    def test_fresh_data_waits_until_the_configured_threshold(self) -> None:
        now = datetime.now(timezone.utc)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs(started_at, completed_at, status, force)
                VALUES (?, ?, 'success', 0)
                """,
                (utc_iso(now - timedelta(seconds=2)), utc_iso(now)),
            )
        service = FakeSyncService()
        coordinator, scheduler = self.scheduler(service, has_data=lambda: True)
        try:
            scheduler.start()
            self.assertTrue(wait_until(lambda: scheduler.snapshot()["next_check_at"] is not None))
            time.sleep(0.12)
            self.assertEqual(service.calls, 0)
            due = datetime.fromisoformat(
                scheduler.snapshot()["next_check_at"].replace("Z", "+00:00")
            )
            self.assertGreater(due, now + timedelta(hours=5, minutes=59))
        finally:
            coordinator.begin_closing(require_idle=False)
            scheduler.stop()

    def test_stale_data_starts_an_immediate_check(self) -> None:
        now = datetime.now(timezone.utc)
        stale = now - timedelta(hours=7)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs(started_at, completed_at, status, force)
                VALUES (?, ?, 'success', 0)
                """,
                (utc_iso(stale - timedelta(minutes=1)), utc_iso(stale)),
            )
        service = FakeSyncService()
        coordinator, scheduler = self.scheduler(service, has_data=lambda: True)
        try:
            scheduler.start()
            self.assertTrue(wait_until(lambda: service.calls == 1))
            self.assertTrue(coordinator.wait_for_idle(timeout=2))
            self.assertEqual(service.forces, [False])
        finally:
            coordinator.begin_closing(require_idle=False)
            scheduler.stop()

    def test_failure_retries_with_backoff_and_never_overlaps(self) -> None:
        service = FakeSyncService(failures=1)
        coordinator, scheduler = self.scheduler(
            service,
            has_data=lambda: service.calls >= 2,
        )
        try:
            scheduler.start()
            self.assertTrue(wait_until(lambda: service.calls >= 2))
            self.assertTrue(coordinator.wait_for_idle(timeout=2))
            self.assertEqual(service.forces[:2], [False, False])
            self.assertEqual(service.maximum_active, 1)
            self.assertGreaterEqual(service.call_times[1] - service.call_times[0], 0.04)
            self.assertTrue(wait_until(lambda: scheduler.snapshot()["last_error"] is None))
            self.assertEqual(scheduler.snapshot()["consecutive_failures"], 0)
        finally:
            coordinator.begin_closing(require_idle=False)
            scheduler.stop()

    def test_runtime_reconfiguration_recomputes_next_check(self) -> None:
        now = datetime.now(timezone.utc)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs(started_at, completed_at, status, force)
                VALUES (?, ?, 'success', 0)
                """,
                (utc_iso(now - timedelta(seconds=1)), utc_iso(now)),
            )
        service = FakeSyncService()
        coordinator, scheduler = self.scheduler(service, has_data=lambda: True)
        try:
            scheduler.start()
            self.assertTrue(wait_until(lambda: scheduler.snapshot()["next_check_at"] is not None))
            updated = self.settings.update_auto_sync(
                {"enabled": True, "interval_hours": 24}
            )
            scheduler.reconfigure(updated)
            self.assertTrue(
                wait_until(lambda: scheduler.snapshot()["interval_hours"] == 24)
            )
            self.assertTrue(wait_until(lambda: scheduler.snapshot()["next_check_at"] is not None))
            due = datetime.fromisoformat(
                scheduler.snapshot()["next_check_at"].replace("Z", "+00:00")
            )
            self.assertGreater(due, now + timedelta(hours=23, minutes=59))

            disabled = self.settings.update_auto_sync(
                {"enabled": False, "interval_hours": 24}
            )
            scheduler.reconfigure(disabled)
            self.assertTrue(wait_until(lambda: scheduler.snapshot()["enabled"] is False))
            self.assertTrue(wait_until(lambda: scheduler.snapshot()["next_check_at"] is None))
        finally:
            coordinator.begin_closing(require_idle=False)
            scheduler.stop()

    def test_restart_keeps_success_older_than_failure_query_window(self) -> None:
        now = datetime.now(timezone.utc)
        success = now - timedelta(days=10)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs(started_at, completed_at, status, force)
                VALUES (?, ?, 'success', 0)
                """,
                (utc_iso(success - timedelta(minutes=1)), utc_iso(success)),
            )
            for index in range(101):
                failed = now - timedelta(hours=101 - index)
                connection.execute(
                    """
                    INSERT INTO sync_runs(
                        started_at, completed_at, status, force, error
                    ) VALUES (?, ?, 'failed', 0, ?)
                    """,
                    (
                        utc_iso(failed - timedelta(minutes=1)),
                        utc_iso(failed),
                        f"failure {index}",
                    ),
                )
        service = FakeSyncService()
        coordinator, scheduler = self.scheduler(service, has_data=lambda: True)
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["last_success_at"], utc_iso(success))
        self.assertEqual(snapshot["consecutive_failures"], 101)
        self.assertEqual(snapshot["last_error"], "failure 100")
        coordinator.begin_closing(require_idle=False)
        scheduler.stop()


class SynchronisationSafetyTests(unittest.TestCase):
    def test_process_lock_prevents_two_coordinators_from_overlapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "runtime" / "fca-sync.lock"
            first_service = FakeSyncService(blocking=True)
            second_service = FakeSyncService()
            first = SyncCoordinator(
                first_service,
                process_lock=ProcessFileLock(lock_path),
            )
            second = SyncCoordinator(
                second_service,
                process_lock=ProcessFileLock(lock_path),
            )
            self.assertTrue(first.start(force=False))
            self.assertTrue(first_service.started.wait(1))
            self.assertFalse(second.start(force=False))
            self.assertEqual(second_service.calls, 0)

            first_service.release.set()
            self.assertTrue(first.wait_for_idle(timeout=2))
            self.assertTrue(second.start(force=False))
            self.assertTrue(second.wait_for_idle(timeout=2))
            self.assertEqual(second_service.calls, 1)

    def test_closing_is_atomic_and_drains_the_active_writer(self) -> None:
        service = FakeSyncService(blocking=True)
        coordinator = SyncCoordinator(service)
        self.assertTrue(coordinator.start(force=False))
        self.assertTrue(service.started.wait(1))
        self.assertFalse(coordinator.begin_closing(require_idle=True))
        self.assertFalse(coordinator.snapshot()["closing"])

        self.assertTrue(coordinator.begin_closing(require_idle=False))
        self.assertTrue(coordinator.snapshot()["closing"])
        with self.assertRaises(APIError) as captured:
            coordinator.run(force=False)
        self.assertEqual(captured.exception.code, "service_closing")
        self.assertFalse(coordinator.wait_for_idle(timeout=0.05))

        service.release.set()
        self.assertTrue(coordinator.wait_for_idle(timeout=2))
        self.assertFalse(coordinator.snapshot()["running"])


class SettingsStoreTests(unittest.TestCase):
    def test_defaults_and_valid_updates_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(temporary)
            self.assertEqual(store.load().interval_hours, 6)
            self.assertTrue(store.load().enabled)
            updated = store.update_auto_sync(
                {"enabled": False, "interval_hours": 12}
            )
            self.assertFalse(updated.enabled)
            self.assertEqual(updated.interval_hours, 12)
            document = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema"], 1)
            self.assertEqual(document["auto_sync"], updated.to_dict())
            self.assertEqual(list(store.path.parent.glob("*.tmp")), [])

    def test_invalid_or_corrupt_settings_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SettingsStore(temporary)
            with self.assertRaises(SettingsError):
                store.update_auto_sync({"enabled": True, "interval_hours": 7})
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(SettingsError):
                store.load()


if __name__ == "__main__":
    unittest.main()
