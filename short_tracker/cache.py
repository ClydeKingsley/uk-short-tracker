"""Small atomic JSON stores for optional price data and user-reviewed symbols."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Mapping


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AtomicJsonStore:
    """Thread-safe UTF-8 JSON persistence using same-directory atomic replace."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def read(self, path: Path, default: Any) -> Any:
        with self._lock:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return default

    def write(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        with self._lock:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
            temp_path.replace(path)


class PriceCache:
    """Cache provider payloads without mixing them into authoritative FCA tables."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.cache_dir = self.data_dir / "cache" / "prices"
        self.mapping_path = self.data_dir / "settings" / "price-symbols.json"
        self.store = AtomicJsonStore()

    @staticmethod
    def _cache_key(symbol: str, kind: str) -> str:
        normalized = symbol.strip().upper()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        safe_symbol = "".join(char if char.isalnum() else "_" for char in normalized)[:32]
        return f"{safe_symbol}-{digest}-{kind}.json"

    def get_payload(self, symbol: str, kind: str, *, max_age_seconds: int) -> dict[str, Any] | None:
        path = self.cache_dir / self._cache_key(symbol, kind)
        envelope = self.store.read(path, {})
        if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
            return None
        cached_at = parse_iso_datetime(envelope.get("cached_at_utc"))
        if cached_at is None:
            return None
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < 0 or age > max_age_seconds:
            return None
        payload = dict(envelope["payload"])
        payload["cache"] = {
            "hit": True,
            "cached_at_utc": envelope.get("cached_at_utc"),
            "age_seconds": max(0, round(age)),
        }
        return payload

    def put_payload(self, symbol: str, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        path = self.cache_dir / self._cache_key(symbol, kind)
        cached_at = utc_now_iso()
        clean_payload = dict(payload)
        clean_payload.pop("cache", None)
        self.store.write(
            path,
            {
                "symbol": symbol.strip().upper(),
                "kind": kind,
                "cached_at_utc": cached_at,
                "payload": clean_payload,
            },
        )
        clean_payload["cache"] = {"hit": False, "cached_at_utc": cached_at, "age_seconds": 0}
        return clean_payload

    def all_symbol_mappings(self) -> dict[str, dict[str, Any]]:
        value = self.store.read(self.mapping_path, {"mappings": {}})
        mappings = value.get("mappings", {}) if isinstance(value, dict) else {}
        return dict(mappings) if isinstance(mappings, dict) else {}

    def get_symbol_mapping(self, issuer_id: int | str) -> dict[str, Any] | None:
        value = self.all_symbol_mappings().get(str(issuer_id))
        return dict(value) if isinstance(value, dict) else None

    def set_symbol_mapping(
        self,
        issuer_id: int | str,
        *,
        symbol: str,
        source: str,
        display_name: str | None = None,
        review_recommended: bool = False,
    ) -> dict[str, Any]:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        root = self.store.read(self.mapping_path, {"version": 1, "mappings": {}})
        if not isinstance(root, dict):
            root = {"version": 1, "mappings": {}}
        mappings = root.setdefault("mappings", {})
        if not isinstance(mappings, dict):
            mappings = {}
            root["mappings"] = mappings
        record = {
            "symbol": normalized,
            "source": source,
            "display_name": display_name,
            "review_recommended": bool(review_recommended),
            "updated_at_utc": utc_now_iso(),
        }
        mappings[str(issuer_id)] = record
        root["version"] = 1
        self.store.write(self.mapping_path, root)
        return dict(record)
