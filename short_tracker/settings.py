"""Persistent, validated settings for the local Short Tracker service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping
import uuid


SETTINGS_SCHEMA = 1
ALLOWED_SYNC_INTERVAL_HOURS = (6, 12, 24)
DEFAULT_SYNC_INTERVAL_HOURS = 6


class SettingsError(ValueError):
    """A settings file or requested settings update is invalid."""


@dataclass(frozen=True, slots=True)
class AutoSyncSettings:
    enabled: bool = True
    interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_hours": self.interval_hours,
        }


def validate_auto_sync(value: Mapping[str, Any]) -> AutoSyncSettings:
    if set(value) != {"enabled", "interval_hours"}:
        raise SettingsError("auto_sync must contain exactly enabled and interval_hours")
    enabled = value.get("enabled")
    interval = value.get("interval_hours")
    if not isinstance(enabled, bool):
        raise SettingsError("auto_sync.enabled must be a boolean")
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise SettingsError("auto_sync.interval_hours must be an integer")
    if interval not in ALLOWED_SYNC_INTERVAL_HOURS:
        allowed = ", ".join(str(item) for item in ALLOWED_SYNC_INTERVAL_HOURS)
        raise SettingsError(f"auto_sync.interval_hours must be one of: {allowed}")
    return AutoSyncSettings(enabled=enabled, interval_hours=interval)


class SettingsStore:
    """Read and atomically replace the small application settings document."""

    def __init__(self, data_dir: str | Path) -> None:
        self.path = Path(data_dir) / "settings" / "application.json"
        self._lock = threading.RLock()

    @staticmethod
    def default() -> AutoSyncSettings:
        return AutoSyncSettings()

    def load(self) -> AutoSyncSettings:
        with self._lock:
            if not self.path.exists():
                settings = self.default()
                self._write(settings)
                return settings
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise SettingsError(f"settings file could not be read: {exc}") from exc
            if not isinstance(document, dict):
                raise SettingsError("settings document must be a JSON object")
            if document.get("schema") != SETTINGS_SCHEMA:
                raise SettingsError("settings document has an unsupported schema")
            auto_sync = document.get("auto_sync")
            if not isinstance(auto_sync, dict):
                raise SettingsError("settings document is missing auto_sync")
            return validate_auto_sync(auto_sync)

    def update_auto_sync(self, value: Mapping[str, Any]) -> AutoSyncSettings:
        settings = validate_auto_sync(value)
        with self._lock:
            self._write(settings)
        return settings

    def _write(self, settings: AutoSyncSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        document = {
            "schema": SETTINGS_SCHEMA,
            "auto_sync": settings.to_dict(),
        }
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = [
    "ALLOWED_SYNC_INTERVAL_HOURS",
    "AutoSyncSettings",
    "DEFAULT_SYNC_INTERVAL_HOURS",
    "SettingsError",
    "SettingsStore",
    "validate_auto_sync",
]
