"""Periodic FCA synchronisation scheduler with bounded retry backoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable, Mapping, Protocol

from .db import Database
from .settings import AutoSyncSettings, SettingsStore


BUSY_RETRY_SECONDS = 60.0
FAILURE_BACKOFF_BASE_SECONDS = 5 * 60.0
FAILURE_BACKOFF_MAX_SECONDS = 60 * 60.0
SETTINGS_RELOAD_SECONDS = 60.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SyncController(Protocol):
    def start(self, *, force: bool = False) -> bool: ...

    def snapshot(self) -> dict[str, Any]: ...

    def add_completion_listener(
        self, listener: Callable[[Mapping[str, Any]], None]
    ) -> None: ...


class AutoSyncScheduler:
    """Schedule non-forced sync checks without overlapping manual work."""

    def __init__(
        self,
        settings: SettingsStore,
        coordinator: SyncController,
        db: Database,
        has_data: Callable[[], bool],
        *,
        now: Callable[[], datetime] | None = None,
        busy_retry_seconds: float = BUSY_RETRY_SECONDS,
        failure_backoff_base_seconds: float = FAILURE_BACKOFF_BASE_SECONDS,
        failure_backoff_max_seconds: float = FAILURE_BACKOFF_MAX_SECONDS,
        settings_reload_seconds: float = SETTINGS_RELOAD_SECONDS,
    ) -> None:
        self.settings_store = settings
        self.coordinator = coordinator
        self.db = db
        self.has_data = has_data
        self._now = now or _utc_now
        self.busy_retry_seconds = max(float(busy_retry_seconds), 0.01)
        self.failure_backoff_base_seconds = max(
            float(failure_backoff_base_seconds), 0.01
        )
        self.failure_backoff_max_seconds = max(
            float(failure_backoff_max_seconds), self.failure_backoff_base_seconds
        )
        self.settings_reload_seconds = max(float(settings_reload_seconds), 0.05)
        self._state_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._settings = self.settings_store.load()
        self._last_attempt_at: str | None = None
        self._last_success_at: str | None = None
        self._last_attempt_time: datetime | None = None
        self._last_success_time: datetime | None = None
        self._next_check_at: str | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._load_persisted_history()
        self.coordinator.add_completion_listener(self._sync_completed)

    def _load_persisted_history(self) -> None:
        newest = self.db.query_one(
            """
            SELECT started_at, completed_at, status, error
              FROM sync_runs
             ORDER BY id DESC
             LIMIT 1
            """
        )
        if newest is None:
            return
        self._last_attempt_at = newest["started_at"]
        # Backoff is measured from completion when available.  Keeping the
        # public attempt timestamp as the actual start preserves its meaning,
        # while a long-running failed attempt cannot consume its retry delay
        # before it has even completed.
        self._last_attempt_time = _parse_iso(
            newest["completed_at"] or newest["started_at"]
        )
        if newest["status"] == "failed":
            self._last_error = str(newest["error"] or "FCA sync failed")[:1000]
        elif newest["status"] == "running":
            self._last_error = "A previous FCA sync did not complete before shutdown."
        latest_success = self.db.query_one(
            """
            SELECT id, completed_at
              FROM sync_runs
             WHERE status = 'success'
             ORDER BY id DESC
             LIMIT 1
            """
        )
        if latest_success is not None:
            self._last_success_at = latest_success["completed_at"]
            self._last_success_time = _parse_iso(self._last_success_at)
        failure_count = self.db.query_one(
            """
            SELECT COUNT(*) AS count
              FROM sync_runs
             WHERE id > COALESCE(
                       (SELECT MAX(id) FROM sync_runs WHERE status = 'success'),
                       0
                   )
               AND status IN ('failed', 'running')
            """
        )
        self._consecutive_failures = int(failure_count["count"] or 0)

    def start(self) -> bool:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if self.coordinator.snapshot().get("closing") is True:
                return False
            self._stop.clear()
            self._wake.clear()
            thread = threading.Thread(
                target=self._run,
                name="fca-auto-sync-scheduler",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return True

    def stop(self, *, timeout: float | None = None) -> bool:
        self._stop.set()
        self._wake.set()
        with self._state_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None
                self._next_check_at = None
        return stopped

    def reconfigure(self, settings: AutoSyncSettings) -> None:
        with self._state_lock:
            self._settings = settings
            self._next_check_at = None
        self._wake.set()

    def _failure_delay(self, failures: int) -> float:
        exponent = min(max(int(failures) - 1, 0), 20)
        return min(
            self.failure_backoff_base_seconds * (2**exponent),
            self.failure_backoff_max_seconds,
        )

    def _due_at(self, now: datetime, settings: AutoSyncSettings) -> datetime | None:
        if not settings.enabled:
            return None
        last_attempt = self._last_attempt_time
        last_success = self._last_success_time
        if self._consecutive_failures and last_attempt is not None:
            return last_attempt + timedelta(
                seconds=self._failure_delay(self._consecutive_failures)
            )
        if not self.has_data():
            # A brand-new profile checks immediately.  If an upstream call
            # reports success without producing every required dataset, avoid
            # a tight success loop and retry on the base backoff instead.
            return (
                last_attempt + timedelta(seconds=self.failure_backoff_base_seconds)
                if last_attempt is not None
                else now
            )
        if last_success is None:
            return now
        return last_success + timedelta(hours=settings.interval_hours)

    def _sync_completed(self, snapshot: Mapping[str, Any]) -> None:
        completed_time = self._now()
        with self._state_lock:
            self._last_attempt_at = snapshot.get("started_at") or self._last_attempt_at
            self._last_attempt_time = completed_time
            error = snapshot.get("last_error")
            if isinstance(error, str) and error:
                self._last_error = error[:1000]
                self._consecutive_failures += 1
            else:
                self._last_success_at = snapshot.get("completed_at") or _iso(completed_time)
                self._last_success_time = completed_time
                self._last_error = None
                self._consecutive_failures = 0
            self._next_check_at = None
        self._wake.set()

    def _wait(self, seconds: float) -> None:
        self._wake.wait(max(min(seconds, self.settings_reload_seconds), 0.01))
        self._wake.clear()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                settings = self.settings_store.load()
            except Exception as exc:
                with self._state_lock:
                    self._last_error = f"Settings error: {exc}"[:1000]
                    self._consecutive_failures += 1
                    due = self._now() + timedelta(
                        seconds=self._failure_delay(self._consecutive_failures)
                    )
                    self._next_check_at = _iso(due)
                self._wait(self._failure_delay(self._consecutive_failures))
                continue

            with self._state_lock:
                self._settings = settings
            coordinator_state = self.coordinator.snapshot()
            if coordinator_state.get("closing") is True:
                break
            if not settings.enabled:
                with self._state_lock:
                    self._next_check_at = None
                self._wait(self.settings_reload_seconds)
                continue
            if coordinator_state.get("running") is True:
                with self._state_lock:
                    self._next_check_at = None
                self._wait(1.0)
                continue

            now = self._now()
            with self._state_lock:
                due = self._due_at(now, settings)
                self._next_check_at = _iso(due) if due is not None else None
            if due is None:
                self._wait(self.settings_reload_seconds)
                continue
            remaining = (due - now).total_seconds()
            if remaining > 0:
                self._wait(remaining)
                continue

            try:
                started = self.coordinator.start(force=False)
            except Exception as exc:
                if self.coordinator.snapshot().get("closing") is True:
                    break
                with self._state_lock:
                    self._last_attempt_at = _iso(now)
                    self._last_attempt_time = now
                    self._last_error = " ".join(str(exc).split())[:1000] or type(exc).__name__
                    self._consecutive_failures += 1
                    retry = now + timedelta(
                        seconds=self._failure_delay(self._consecutive_failures)
                    )
                    self._next_check_at = _iso(retry)
                self._wait(self._failure_delay(self._consecutive_failures))
                continue
            if started:
                with self._state_lock:
                    state = self.coordinator.snapshot()
                    self._last_attempt_at = state.get("started_at") or _iso(now)
                    self._last_attempt_time = now
                    self._next_check_at = None
                self._wait(1.0)
            else:
                retry = now + timedelta(seconds=self.busy_retry_seconds)
                with self._state_lock:
                    self._next_check_at = _iso(retry)
                self._wait(self.busy_retry_seconds)

        with self._state_lock:
            self._next_check_at = None

    def snapshot(self) -> dict[str, Any]:
        coordinator = self.coordinator.snapshot()
        with self._state_lock:
            return {
                "enabled": self._settings.enabled,
                "interval_hours": self._settings.interval_hours,
                "running": bool(coordinator.get("running")),
                "closing": bool(coordinator.get("closing")),
                "last_attempt_at": self._last_attempt_at,
                "last_success_at": self._last_success_at,
                "next_check_at": self._next_check_at,
                "last_error": self._last_error,
                "consecutive_failures": self._consecutive_failures,
            }


__all__ = ["AutoSyncScheduler"]
