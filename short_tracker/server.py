"""Local HTTP application for the UK Short Tracker.

The server intentionally binds to a loopback address and exposes only read-only
research APIs plus narrowly scoped local mutations: refreshing public data,
saving a user-reviewed market-price symbol, and updating automatic-refresh
preferences.  It has no broker integration or order-execution surface.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import socket
import threading
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .cache import PriceCache
from .db import Database
from .fca import ANSP_REGIME_START, FCADataError, FCADataService
from .prices import (
    MarketPriceProvider,
    PriceAdapterError,
    YahooFinanceProvider,
    normalize_symbol,
)
from .process_lock import ProcessFileLock
from .scheduler import AutoSyncScheduler
from .settings import (
    ALLOWED_SYNC_INTERVAL_HOURS,
    SettingsError,
    SettingsStore,
)
from .update import (
    DEFAULT_GITHUB_OWNER,
    DEFAULT_GITHUB_REPOSITORY,
    UpdateChannel,
    UpdateChecker,
    UpdateConfiguration,
)


MAX_JSON_BODY_BYTES = 64 * 1024
PRICE_HISTORY_TTL_SECONDS = 12 * 60 * 60
LATEST_PRICE_TTL_SECONDS = 2 * 60
STALE_PRICE_FALLBACK_SECONDS = 10 * 365 * 24 * 60 * 60
REQUIRED_DATASETS = frozenset(
    {"legacy_named", "ansp_current", "ansp_historic", "reportable_shares"}
)
MAX_QUERY_CHARACTERS = 200
MAX_IDENTIFIER_CHARACTERS = 128
MAX_QUERY_FIELDS = 50
MAX_TICKER_LOOKUP_CANDIDATES = 5
_SHORT_TICKER_RE = re.compile(
    r"(?=.{2,16}\Z)(?=.*[A-Z])[A-Z0-9]{1,10}(?:[.-][A-Z0-9]{1,5}){0,2}"
)


class APIError(RuntimeError):
    """An expected API error with an HTTP status and safe public message."""

    def __init__(self, status: int, message: str, *, code: str = "request_error") -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message
        self.code = code


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _public_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:1000] or type(exc).__name__


class SyncCoordinator:
    """Serialise FCA syncs and expose progress without blocking the UI."""

    def __init__(
        self,
        service: FCADataService,
        *,
        process_lock: ProcessFileLock | None = None,
    ) -> None:
        self.service = service
        self._state_lock = threading.RLock()
        self._idle = threading.Condition(self._state_lock)
        self._process_lock = process_lock
        self._running = False
        self._closing = False
        self._started_at: str | None = None
        self._completed_at: str | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._completion_listeners: list[Callable[[Mapping[str, Any]], None]] = []

    def _reserve(self) -> bool:
        with self._state_lock:
            if self._closing:
                raise APIError(
                    HTTPStatus.CONFLICT,
                    "服务正在安全关闭，不能开始新的同步。",
                    code="service_closing",
                )
            if self._running:
                return False
            if self._process_lock is not None and not self._process_lock.try_acquire():
                return False
            self._running = True
            self._started_at = _utc_now_iso()
            self._completed_at = None
            self._last_error = None
        return True

    def add_completion_listener(
        self, listener: Callable[[Mapping[str, Any]], None]
    ) -> None:
        with self._state_lock:
            self._completion_listeners.append(listener)

    def begin_closing(self, *, require_idle: bool) -> bool:
        """Atomically reject future syncs, optionally only when already idle."""

        with self._idle:
            if self._closing:
                return True
            if require_idle and self._running:
                return False
            self._closing = True
            self._idle.notify_all()
            return True

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: not self._running, timeout=timeout)

    def _run_reserved(self, *, force: bool) -> dict[str, Any]:
        try:
            result = self.service.sync(force=force)
            with self._state_lock:
                self._last_result = result
            return result
        except Exception as exc:
            with self._state_lock:
                self._last_error = _public_error(exc)
            raise
        finally:
            with self._idle:
                self._running = False
                self._completed_at = _utc_now_iso()
                if self._process_lock is not None:
                    self._process_lock.release()
                completed = self._snapshot_unlocked()
                listeners = tuple(self._completion_listeners)
                self._idle.notify_all()
            for listener in listeners:
                try:
                    listener(completed)
                except Exception:
                    # Monitoring callbacks must never change sync success or
                    # strand the process-wide reservation.
                    pass

    def run(self, *, force: bool = False) -> dict[str, Any]:
        if not self._reserve():
            raise APIError(HTTPStatus.CONFLICT, "已有同步任务正在运行。", code="sync_running")
        return self._run_reserved(force=force)

    def start(self, *, force: bool = False) -> bool:
        # Reserve synchronously before starting the worker.  Merely checking
        # lock state here leaves a race in which two HTTP threads can both
        # return 202 before either worker has acquired the lock.
        if not self._reserve():
            return False

        def worker() -> None:
            try:
                self._run_reserved(force=force)
            except Exception:
                # The public status endpoint exposes the safe error.  A failed
                # refresh never advances FCA dataset heads.
                pass

        thread = threading.Thread(target=worker, name="fca-sync", daemon=True)
        try:
            thread.start()
        except Exception as exc:
            with self._idle:
                self._running = False
                self._completed_at = _utc_now_iso()
                self._last_error = _public_error(exc)
                if self._process_lock is not None:
                    self._process_lock.release()
                self._idle.notify_all()
            raise
        return True

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "closing": self._closing,
            "started_at": self._started_at,
            "completed_at": self._completed_at,
            "last_result": self._last_result,
            "last_error": self._last_error,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return self._snapshot_unlocked()


@dataclass(slots=True)
class ShortTrackerApplication:
    root_dir: Path
    data_dir: Path
    web_dir: Path
    db: Database
    fca: FCADataService
    prices: MarketPriceProvider
    price_cache: PriceCache
    sync: SyncCoordinator
    settings: SettingsStore | None = None
    auto_sync: AutoSyncScheduler | None = None
    update_checker: UpdateChecker | None = None
    instance_id: str | None = None

    @classmethod
    def create(
        cls,
        root_dir: str | Path,
        *,
        data_dir: str | Path | None = None,
        price_provider: MarketPriceProvider | None = None,
        instance_id: str | None = None,
    ) -> "ShortTrackerApplication":
        root = Path(root_dir).resolve()
        data = Path(data_dir).resolve() if data_dir else root / "data"
        data.mkdir(parents=True, exist_ok=True)
        db = Database(data / "short_tracker.sqlite")
        fca = FCADataService(db, data)
        settings = SettingsStore(data)
        settings.load()
        coordinator = SyncCoordinator(
            fca,
            process_lock=ProcessFileLock(data / "runtime" / "fca-sync.lock"),
        )
        app = cls(
            root_dir=root,
            data_dir=data,
            web_dir=root / "web",
            db=db,
            fca=fca,
            prices=price_provider or YahooFinanceProvider(),
            price_cache=PriceCache(data),
            sync=coordinator,
            settings=settings,
            update_checker=UpdateChecker(
                data / "settings" / "update-check.json",
                config=UpdateConfiguration(
                    owner=DEFAULT_GITHUB_OWNER,
                    repository=DEFAULT_GITHUB_REPOSITORY,
                    channel=UpdateChannel.STABLE,
                ),
            ),
            instance_id=instance_id,
        )
        app.auto_sync = AutoSyncScheduler(settings, coordinator, db, app.has_data)
        return app

    def has_data(self) -> bool:
        rows = self.db.query_all("SELECT dataset_key FROM dataset_heads")
        return REQUIRED_DATASETS.issubset({row["dataset_key"] for row in rows})

    def get_settings(self) -> dict[str, Any]:
        if self.settings is None:
            raise RuntimeError("application settings are unavailable")
        settings = self.settings.load()
        return {
            "ok": True,
            "auto_sync": {
                **settings.to_dict(),
                "allowed_interval_hours": list(ALLOWED_SYNC_INTERVAL_HOURS),
            },
        }

    def update_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.sync.snapshot().get("closing") is True:
            raise APIError(
                HTTPStatus.CONFLICT,
                "服务正在安全关闭，不能修改设置。",
                code="service_closing",
            )
        if set(payload) != {"auto_sync"} or not isinstance(payload.get("auto_sync"), dict):
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "设置负载必须只包含 auto_sync 对象。",
                code="invalid_settings",
            )
        if self.settings is None:
            raise RuntimeError("application settings are unavailable")
        try:
            settings = self.settings.update_auto_sync(payload["auto_sync"])
        except SettingsError as exc:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                str(exc),
                code="invalid_settings",
            ) from exc
        if self.auto_sync is not None:
            self.auto_sync.reconfigure(settings)
        return self.get_settings()

    def _updates(self) -> UpdateChecker:
        """Return the configured checker, or a safe disabled checker for fixtures."""

        if self.update_checker is None:
            self.update_checker = UpdateChecker(
                self.data_dir / "settings" / "update-check.json"
            )
        return self.update_checker

    def get_update_status(self) -> dict[str, Any]:
        """Read cached update metadata without performing network I/O."""

        return self._updates().status(current_version=__version__).to_dict()

    def check_for_updates(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Perform a bounded metadata-only GitHub Release check when configured."""

        if self.sync.snapshot().get("closing") is True:
            raise APIError(
                HTTPStatus.CONFLICT,
                "服务正在安全关闭，不能检查更新。",
                code="service_closing",
            )
        if set(payload) != {"force"} or not isinstance(payload.get("force"), bool):
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "更新检查负载必须只包含布尔值 force。",
                code="invalid_update_check",
            )
        return self._updates().check(
            current_version=__version__,
            force=payload["force"],
        ).to_dict()

    def start_background_services(self) -> None:
        if self.auto_sync is not None:
            self.auto_sync.start()

    def begin_closing(self, *, require_idle: bool) -> bool:
        accepted = self.sync.begin_closing(require_idle=require_idle)
        if accepted and self.auto_sync is not None:
            self.auto_sync.stop()
        return accepted

    def drain(self) -> None:
        if self.auto_sync is not None:
            self.auto_sync.stop()
        self.sync.wait_for_idle(timeout=None)

    def status(self) -> dict[str, Any]:
        sync_row = self.db.query_one(
            """
            SELECT id, started_at, completed_at, status, force, error
              FROM sync_runs
             ORDER BY id DESC LIMIT 1
            """
        )
        successful_sync_row = self.db.query_one(
            """
            SELECT completed_at
              FROM sync_runs
             WHERE status = 'success'
             ORDER BY id DESC LIMIT 1
            """
        )
        issuer_row = self.db.query_one("SELECT COUNT(*) AS count FROM issuers")
        head_rows = self.db.query_all(
            """
            SELECT h.dataset_key, h.activated_at, s.source_url, s.last_checked_at,
                   i.row_count, i.profile_json
              FROM dataset_heads h
              JOIN raw_snapshots s ON s.id = h.snapshot_id
              JOIN import_runs i ON i.id = h.import_run_id
             ORDER BY h.dataset_key
            """
        )
        datasets: dict[str, Any] = {}
        for row in head_rows:
            try:
                profile = json.loads(row["profile_json"] or "{}")
            except json.JSONDecodeError:
                profile = {}
            datasets[row["dataset_key"]] = {
                "row_count": int(row["row_count"] or 0),
                "activated_at": row["activated_at"],
                "last_checked_at": row["last_checked_at"],
                "url": row["source_url"],
                "profile": profile,
            }
        live = self.sync.snapshot()
        last_sync_at = successful_sync_row["completed_at"] if successful_sync_row else None
        automatic = (
            self.auto_sync.snapshot()
            if self.auto_sync is not None
            else {
                "enabled": False,
                "interval_hours": None,
                "running": live["running"],
                "closing": live.get("closing", False),
                "last_attempt_at": live.get("started_at"),
                "last_success_at": last_sync_at,
                "next_check_at": None,
                "last_error": live.get("last_error"),
                "consecutive_failures": 0,
            }
        )
        ready = REQUIRED_DATASETS.issubset(datasets)
        return {
            "ok": True,
            "ready": ready,
            "status": (
                "closing"
                if live.get("closing")
                else "syncing"
                if live["running"]
                else "ready"
                if ready
                else "not_ready"
            ),
            "service": "UK Short Tracker",
            "mode": "local_read_only_research",
            "version": __version__,
            "protocol": 1,
            "instance_id": self.instance_id,
            "server_time_utc": _utc_now_iso(),
            "last_sync_at": last_sync_at,
            "source": "FCA official public short-position datasets",
            "security_count": int(issuer_row["count"] or 0) if issuer_row else 0,
            "datasets": datasets,
            "auto_sync": automatic,
            "sync": {
                **live,
                "database_last_run": dict(sync_row) if sync_row else None,
            },
            "regime_start": ANSP_REGIME_START,
        }

    def search_securities(self, query: str, limit: int) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 100)
        ticker_query = self._ticker_query(query)
        selected: list[dict[str, Any]] = []
        seen: set[int] = set()
        warnings: list[str] = []

        # User-reviewed/previously resolved mappings are local, cheap, and
        # authoritative for this application.  Exact symbols rank before
        # prefixes (for example OCDO.L before other OCDO.* instruments).
        if ticker_query:
            local_matches: list[tuple[int, str, int]] = []
            for issuer_id, mapping in self.price_cache.all_symbol_mappings().items():
                if not isinstance(mapping, Mapping):
                    continue
                symbol = str(mapping.get("symbol") or "").strip().upper()
                if symbol == ticker_query:
                    local_matches.append((0, symbol, int(issuer_id)))
                elif symbol.startswith(ticker_query):
                    local_matches.append((1, symbol, int(issuer_id)))
            local_matches.sort(key=lambda item: (item[0], item[1], item[2]))
            for match_rank, symbol, issuer_id in local_matches:
                security = self.fca.get_security(issuer_id)
                if security is None or int(security["id"]) in seen:
                    continue
                selected.append(
                    self._search_record(
                        security,
                        ticker=symbol,
                        rank=-2 + match_rank,
                        ticker_source="local_mapping",
                    )
                )
                seen.add(int(security["id"]))
                if len(selected) >= limit:
                    break

        # FCA name and ISIN search remains the normal, fully offline path.
        if len(selected) < limit:
            for item in self.fca.search(query, limit=limit):
                issuer_id = int(item["id"])
                if issuer_id in seen:
                    continue
                selected.append(self._search_record(item))
                seen.add(issuer_id)
                if len(selected) >= limit:
                    break

        # Only an otherwise-empty, short ticker-like query is eligible for one
        # best-effort provider lookup.  Candidate symbols are not persisted;
        # their display names are mapped back through the FCA identity index.
        if not selected and ticker_query:
            try:
                candidates = self.prices.suggest_uk_symbols(
                    ticker_query,
                    limit=MAX_TICKER_LOOKUP_CANDIDATES,
                )
            except (PriceAdapterError, ValueError) as exc:
                warnings.append(f"Ticker 候选查询不可用：{_public_error(exc)}")
                candidates = ()
            for candidate in candidates:
                symbol = str(getattr(candidate, "symbol", "") or "").strip().upper()
                display_name = str(getattr(candidate, "display_name", "") or "").strip()
                if not display_name or not self._candidate_symbol_matches(ticker_query, symbol):
                    continue
                for match in self.fca.search(
                    display_name,
                    limit=min(MAX_TICKER_LOOKUP_CANDIDATES, limit - len(selected)),
                ):
                    issuer_id = int(match["id"])
                    if issuer_id in seen:
                        continue
                    selected.append(
                        self._search_record(
                            match,
                            ticker=symbol,
                            ticker_source="price_provider_suggestion",
                        )
                    )
                    seen.add(issuer_id)
                    break
                if len(selected) >= limit:
                    break

        result = {"query": query, "count": len(selected), "items": selected}
        if warnings:
            result["warnings"] = warnings
        return result

    def get_current_rankings(
        self,
        *,
        query: str = "",
        sort: str = "short_percent",
        order: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Return a searchable, deterministic view of the active FCA ANSP table.

        The source dataset is small enough to enrich once with locally reviewed
        ticker mappings.  Pagination is applied only after filtering and sorting
        so the API remains useful both to the browser dashboard and local scripts.
        ``rank`` always means the position in the canonical ANSP-descending order,
        even when the caller temporarily sorts the table by another column.
        """

        allowed_sorts = {"short_percent", "name", "position_date"}
        if sort not in allowed_sorts:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "sort 必须是 short_percent、name 或 position_date。",
                code="invalid_sort",
            )
        if order not in {"asc", "desc"}:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "order 必须是 asc 或 desc。",
                code="invalid_order",
            )
        if page < 1:
            raise APIError(HTTPStatus.BAD_REQUEST, "page 必须大于或等于 1。", code="invalid_page")
        if page_size < 1 or page_size > 2000:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "page_size 必须介于 1 和 2000 之间。",
                code="invalid_page_size",
            )

        payload = self.fca.get_current_rankings(limit=2000)
        source_items = list(payload.get("items", []))
        source_total = int(payload.get("total") or payload.get("total_count") or len(source_items))
        source_limit = int(payload.get("limit") or 2000)
        source_truncated = bool(payload.get("truncated")) or source_total > len(source_items)
        mappings = self.price_cache.all_symbol_mappings()
        today = datetime.now(timezone.utc).date()
        issuer_row_counts: dict[int, int] = {}
        for source_item in source_items:
            issuer_id = int(
                source_item.get("security_id")
                or source_item.get("issuer_id")
                or source_item.get("id")
            )
            issuer_row_counts[issuer_id] = issuer_row_counts.get(issuer_id, 0) + 1
        items: list[dict[str, Any]] = []
        for source_item in source_items:
            issuer_id = int(
                source_item.get("security_id")
                or source_item.get("issuer_id")
                or source_item.get("id")
            )
            mapping = mappings.get(str(issuer_id)) or {}
            aggregate_bp = int(source_item.get("aggregate_bp") or 0)
            short_percent = source_item.get("short_percent")
            if short_percent is None:
                short_percent = aggregate_bp / 100
            position_date = str(source_item.get("position_date") or "")
            position_age_days = None
            position_date_in_future = False
            try:
                age_days = (today - datetime.strptime(position_date, "%Y-%m-%d").date()).days
                if age_days >= 0:
                    position_age_days = age_days
                else:
                    position_date_in_future = True
            except ValueError:
                pass
            name = str(source_item.get("name") or source_item.get("company_name") or "")
            isin = str(source_item.get("isin") or "")
            mapped_isin = str(mapping.get("isin") or "").strip().upper()
            mapping_is_safe = bool(mapping) and (
                issuer_row_counts.get(issuer_id, 0) == 1
                or (mapped_isin and mapped_isin == isin.upper())
            )
            ticker = (
                str(mapping.get("symbol") or "") or None
                if mapping_is_safe
                else None
            )
            ticker_provenance = None
            if ticker:
                ticker_provenance = {
                    "source": mapping.get("source"),
                    "display_name": mapping.get("display_name"),
                    "updated_at_utc": mapping.get("updated_at_utc"),
                    "review_recommended": bool(mapping.get("review_recommended")),
                    "mapping_scope": (
                        "isin" if mapped_isin else "single_current_ansp_row_for_issuer"
                    ),
                }
            items.append(
                {
                    **source_item,
                    "rank": int(source_item.get("rank") or len(items) + 1),
                    "security_id": issuer_id,
                    "id": issuer_id,
                    "name": name,
                    "company_name": name,
                    "isin": isin,
                    "ticker": ticker,
                    "price_symbol": ticker,
                    "ticker_provenance": ticker_provenance,
                    "market": "UK",
                    "aggregate_bp": aggregate_bp,
                    "short_percent": float(short_percent),
                    "unit": "percent_of_issued_share_capital",
                    "position_date": position_date,
                    "position_age_days": position_age_days,
                    "position_date_in_future": position_date_in_future,
                }
            )

        needle = query.casefold()
        if needle:
            items = [
                item
                for item in items
                if needle
                in " ".join(
                    str(item.get(field) or "")
                    for field in ("name", "isin", "ticker", "security_id")
                ).casefold()
            ]

        # The FCA layer owns the canonical ANSP rank and its SQLite tie rules.
        # Re-establish that order by rank, then rely on Python's stable sort so
        # equal values keep the exact canonical order instead of being subtly
        # reordered by Python/SQLite collation differences.
        items.sort(key=lambda item: int(item.get("rank") or 0))
        reverse = order == "desc"
        if sort == "short_percent":
            items.sort(key=lambda item: float(item["short_percent"]), reverse=reverse)
        elif sort == "position_date":
            items.sort(key=lambda item: str(item.get("position_date") or ""), reverse=reverse)
        else:
            items.sort(
                key=lambda item: (
                    str(item.get("name") or "").casefold(),
                    str(item.get("name") or ""),
                    str(item.get("isin") or ""),
                    int(item.get("row_number") or 0),
                ),
                reverse=reverse,
            )

        total = len(items)
        total_pages = (total + page_size - 1) // page_size if total else 0
        offset = (page - 1) * page_size
        page_items = items[offset : offset + page_size]
        return {
            "ok": True,
            "items": page_items,
            "count": len(page_items),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "sort": sort,
            "order": order,
            "query": query,
            "as_of_date": payload.get("as_of_date"),
            "age_reference_date": today.isoformat(),
            "age_reference_timezone": "UTC",
            "source": payload.get("source"),
            "source_name": "FCA current aggregate net short positions (ANSP)",
            "source_total": source_total,
            "source_limit": source_limit,
            "source_truncated": source_truncated,
            "coverage": payload.get("coverage"),
            "methodology": payload.get("methodology"),
        }

    @staticmethod
    def _ticker_query(query: str) -> str | None:
        candidate = query.strip().upper()
        return candidate if _SHORT_TICKER_RE.fullmatch(candidate) else None

    @staticmethod
    def _candidate_symbol_matches(query: str, symbol: str) -> bool:
        return symbol == query or ("." not in query and symbol.startswith(f"{query}."))

    def _search_record(
        self,
        item: Mapping[str, Any],
        *,
        ticker: str | None = None,
        rank: int | None = None,
        ticker_source: str | None = None,
    ) -> dict[str, Any]:
        issuer_id = int(item["id"])
        mapping = self.price_cache.get_symbol_mapping(issuer_id)
        mapped_symbol = str(mapping.get("symbol")) if mapping and mapping.get("symbol") else None
        current = item.get("current_ansp") or {}
        record = {
            **item,
            "isin": item.get("isin") or item.get("primary_isin"),
            "ticker": mapped_symbol or ticker,
            "market": "UK",
            "current_short_percent": current.get("value"),
        }
        if rank is not None:
            record["rank"] = rank
        if ticker_source:
            record["ticker_source"] = ticker_source
        return record

    def get_security(self, identifier: str) -> dict[str, Any]:
        security = self.fca.get_security(identifier)
        if security is None:
            raise APIError(HTTPStatus.NOT_FOUND, "未找到该证券。", code="security_not_found")
        mapping = self.price_cache.get_symbol_mapping(security["id"])
        enriched = {
            **security,
            "isin": security.get("primary_isin"),
            "ticker": mapping.get("symbol") if mapping else None,
            "price_symbol": mapping.get("symbol") if mapping else None,
            "price_mapping": mapping,
            "market": "UK",
        }
        return {"security": enriched, **enriched}

    def get_short_series(self, identifier: str) -> dict[str, Any]:
        payload = self.fca.get_short_series(identifier)
        if payload is None:
            raise APIError(HTTPStatus.NOT_FOUND, "未找到该证券。", code="security_not_found")
        by_date: dict[str, dict[str, Any]] = {}
        for point in payload["legacy"]:
            record = by_date.setdefault(point["date"], {"date": point["date"]})
            record["legacy_percent"] = point["value"]
            record["legacy"] = point["value"]
        for point in payload["ansp"]:
            record = by_date.setdefault(point["date"], {"date": point["date"]})
            record["ansp_percent"] = point["value"]
            record["ansp"] = point["value"]
        items = [by_date[key] for key in sorted(by_date)]
        checked_at = max(
            (
                str(item["last_checked_at"])
                for item in (payload.get("coverage") or {}).values()
                if isinstance(item, Mapping) and item.get("last_checked_at")
            ),
            default=None,
        )
        return {
            **payload,
            "items": items,
            "series": items,
            "freshness": payload.get("coverage"),
            "source": "FCA",
            "latest_date": items[-1]["date"] if items else None,
            "fetched_at": checked_at,
            "fetched_at_utc": checked_at,
            "regime_start": ANSP_REGIME_START,
        }

    def _resolve_mapping(self, security: Mapping[str, Any]) -> dict[str, Any]:
        issuer_id = security["id"]
        mapping = self.price_cache.get_symbol_mapping(issuer_id)
        if mapping:
            return mapping
        resolution = self.prices.resolve_uk_symbol(
            str(security["name"]),
            isin=security.get("primary_isin"),
            limit=5,
        )
        return self.price_cache.set_symbol_mapping(
            issuer_id,
            symbol=resolution.symbol,
            source="automatic_yahoo_suggestion",
            display_name=resolution.selected.display_name,
            review_recommended=resolution.review_recommended,
        )

    def get_prices(self, identifier: str, *, refresh: bool = False) -> dict[str, Any]:
        security = self.fca.get_security(identifier)
        if security is None:
            raise APIError(HTTPStatus.NOT_FOUND, "未找到该证券。", code="security_not_found")
        try:
            mapping = self._resolve_mapping(security)
            symbol = normalize_symbol(str(mapping["symbol"]))
        except (PriceAdapterError, ValueError) as exc:
            raise APIError(
                HTTPStatus.BAD_GATEWAY,
                f"无法自动匹配行情代码：{_public_error(exc)}",
                code="price_symbol_unresolved",
            ) from exc

        history = None if refresh else self.price_cache.get_payload(
            symbol, "history", max_age_seconds=PRICE_HISTORY_TTL_SECONDS
        )
        latest = None if refresh else self.price_cache.get_payload(
            symbol, "latest", max_age_seconds=LATEST_PRICE_TTL_SECONDS
        )
        stale_history = self.price_cache.get_payload(
            symbol, "history", max_age_seconds=STALE_PRICE_FALLBACK_SECONDS
        )
        stale_latest = self.price_cache.get_payload(
            symbol, "latest", max_age_seconds=STALE_PRICE_FALLBACK_SECONDS
        )
        warnings: list[str] = []

        def fetch_history() -> dict[str, Any]:
            return self.prices.get_daily_history(symbol).to_dict()

        def fetch_latest() -> dict[str, Any]:
            return self.prices.get_latest_price(symbol).to_dict()

        jobs: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="price-fetch") as pool:
            if history is None:
                jobs["history"] = pool.submit(fetch_history)
            if latest is None:
                jobs["latest"] = pool.submit(fetch_latest)
            for kind, future in jobs.items():
                try:
                    result = future.result()
                    cached = self.price_cache.put_payload(symbol, kind, result)
                    if kind == "history":
                        history = cached
                    else:
                        latest = cached
                except Exception as exc:
                    fallback = stale_history if kind == "history" else stale_latest
                    if fallback is None:
                        raise APIError(
                            HTTPStatus.BAD_GATEWAY,
                            f"行情数据读取失败：{_public_error(exc)}",
                            code="price_provider_error",
                        ) from exc
                    fallback.setdefault("cache", {})["stale_fallback"] = True
                    warnings.append(f"{kind} refresh failed; showing cached data: {_public_error(exc)}")
                    if kind == "history":
                        history = fallback
                    else:
                        latest = fallback

        if history is None:
            raise APIError(HTTPStatus.BAD_GATEWAY, "没有可用的历史行情。", code="price_history_empty")
        bars = [dict(item) for item in history.get("bars", []) if isinstance(item, Mapping)]
        for item in bars:
            item["source"] = "Yahoo Finance"
        if latest and latest.get("price") is not None and latest.get("as_of_utc"):
            latest_date = str(latest["as_of_utc"])[:10]
            quote = {
                "date": latest_date,
                "close": latest["price"],
                "adjusted_close": latest["price"],
                "source": "Yahoo Finance",
                "is_latest_quote": True,
            }
            if bars and bars[-1].get("date") == latest_date:
                bars[-1] = {**bars[-1], **quote}
            elif not bars or str(bars[-1].get("date", "")) < latest_date:
                bars.append(quote)
        fetched_at = max(
            (
                str(value)
                for value in (
                    history.get("fetched_at_utc"),
                    (latest or {}).get("fetched_at_utc"),
                )
                if value
            ),
            default=None,
        )
        return {
            "security_id": security["id"],
            "symbol": symbol,
            "mapping": mapping,
            "items": bars,
            "series": bars,
            "history": history,
            "latest": latest,
            "currency": history.get("currency") or (latest or {}).get("currency"),
            "source": "Yahoo Finance",
            "latest_date": str(bars[-1].get("date")) if bars and bars[-1].get("date") else None,
            "fetched_at": fetched_at,
            "fetched_at_utc": fetched_at,
            "warnings": warnings,
        }

    def price_search(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        text = query.strip()
        if len(text) < 2:
            return {"query": text, "count": 0, "items": []}
        manual = text if re.fullmatch(r"[A-Za-z0-9.^=_-]{1,40}", text) and "." in text else None
        try:
            candidates = self.prices.suggest_uk_symbols(
                text,
                manual_symbol=manual,
                limit=min(max(limit, 1), 20),
            )
        except (PriceAdapterError, ValueError) as exc:
            raise APIError(
                HTTPStatus.BAD_GATEWAY,
                f"行情代码搜索失败：{_public_error(exc)}",
                code="price_search_error",
            ) from exc
        items = []
        for candidate in candidates:
            item = candidate.to_dict()
            item["name"] = item["display_name"]
            item["currency"] = None
            items.append(item)
        return {"query": text, "count": len(items), "items": items}

    def save_price_symbol(self, identifier: str, body: Mapping[str, Any]) -> dict[str, Any]:
        security = self.fca.get_security(identifier)
        if security is None:
            raise APIError(HTTPStatus.NOT_FOUND, "未找到该证券。", code="security_not_found")
        try:
            symbol = normalize_symbol(str(body.get("symbol") or ""))
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_symbol") from exc
        mapping = self.price_cache.set_symbol_mapping(
            security["id"],
            symbol=symbol,
            source="user_reviewed",
            display_name=str(body.get("display_name") or body.get("name") or symbol),
            review_recommended=False,
        )
        enriched = {
            **security,
            "isin": security.get("primary_isin"),
            "ticker": symbol,
            "price_symbol": symbol,
            "price_mapping": mapping,
            "market": "UK",
        }
        return {
            "ok": True,
            "security_id": security["id"],
            "mapping": mapping,
            "symbol": symbol,
            "security": enriched,
        }


class ShortTrackerRequestHandler(BaseHTTPRequestHandler):
    server_version = "ShortTracker/0.1"

    @property
    def app(self) -> ShortTrackerApplication:
        return self.server.app  # type: ignore[attr-defined,no-any-return]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {format % args}")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(head_only=False)

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch(head_only=False)

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch(head_only=False)

    def _dispatch(self, *, head_only: bool) -> None:
        try:
            self._validate_local_request()
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                if head_only:
                    raise APIError(HTTPStatus.METHOD_NOT_ALLOWED, "API 不支持 HEAD。")
                try:
                    query = parse_qs(parsed.query, max_num_fields=MAX_QUERY_FIELDS)
                except ValueError as exc:
                    raise APIError(
                        HTTPStatus.BAD_REQUEST,
                        "查询参数过多。",
                        code="too_many_query_fields",
                    ) from exc
                payload, status = self._api(parsed.path, query)
                self._send_json(payload, status=status)
            elif self.command in {"GET", "HEAD"}:
                self._serve_static(parsed.path, head_only=head_only)
            else:
                raise APIError(HTTPStatus.METHOD_NOT_ALLOWED, "不支持的请求方法。")
        except APIError as exc:
            self._send_json(
                {
                    "ok": False,
                    "message": exc.message,
                    "error": {"code": exc.code, "message": exc.message},
                },
                status=exc.status,
            )
        except (FCADataError, PriceAdapterError) as exc:
            message = _public_error(exc)
            self._send_json(
                {
                    "ok": False,
                    "message": message,
                    "error": {"code": "upstream_data_error", "message": message},
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
        except Exception as exc:
            self.log_error("unhandled request error: %r", exc)
            self._send_json(
                {
                    "ok": False,
                    "message": "服务器内部错误。",
                    "error": {
                        "code": "internal_error",
                        "message": "服务器内部错误。",
                    },
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    @staticmethod
    def _loopback_hostname(hostname: str | None) -> bool:
        if not hostname:
            return False
        host = hostname.rstrip(".").casefold()
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def _parsed_local_authority(self, value: str, *, label: str) -> tuple[str, int | None]:
        try:
            parsed = urlparse(f"//{value}")
            port = parsed.port
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, f"{label} 无效。", code="invalid_authority") from exc
        if (
            not self._loopback_hostname(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise APIError(HTTPStatus.FORBIDDEN, "只允许本机访问。", code="local_only")
        return str(parsed.hostname), port

    def _validate_local_request(self) -> None:
        """Reject DNS-rebinding hosts and cross-site browser mutations."""

        peer = str(self.client_address[0]).split("%", 1)[0]
        try:
            if not ipaddress.ip_address(peer).is_loopback:
                raise APIError(HTTPStatus.FORBIDDEN, "只允许本机访问。", code="local_only")
        except ValueError as exc:
            raise APIError(HTTPStatus.FORBIDDEN, "只允许本机访问。", code="local_only") from exc

        host_header = self.headers.get("Host")
        if not host_header:
            raise APIError(HTTPStatus.BAD_REQUEST, "缺少 Host 请求头。", code="invalid_host")
        _, host_port = self._parsed_local_authority(host_header, label="Host")
        expected_port = int(self.server.server_address[1])
        if host_port is not None and host_port != expected_port:
            raise APIError(HTTPStatus.FORBIDDEN, "Host 端口不匹配。", code="invalid_host")

        if self.command not in {"POST", "PUT"}:
            return
        if (self.headers.get("Sec-Fetch-Site") or "").casefold() == "cross-site":
            raise APIError(HTTPStatus.FORBIDDEN, "拒绝跨站请求。", code="cross_site_request")
        origin = self.headers.get("Origin")
        if not origin:
            return  # Allows local scripts and launcher health checks.
        if origin == "null":
            raise APIError(HTTPStatus.FORBIDDEN, "拒绝不透明来源请求。", code="cross_site_request")
        try:
            parsed_origin = urlparse(origin)
            origin_port = parsed_origin.port
        except ValueError as exc:
            raise APIError(HTTPStatus.FORBIDDEN, "Origin 无效。", code="cross_site_request") from exc
        if (
            parsed_origin.scheme != "http"
            or not self._loopback_hostname(parsed_origin.hostname)
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.params
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise APIError(HTTPStatus.FORBIDDEN, "拒绝跨站请求。", code="cross_site_request")
        if (origin_port or 80) != expected_port:
            raise APIError(HTTPStatus.FORBIDDEN, "Origin 端口不匹配。", code="cross_site_request")

    def _api(self, path: str, query: Mapping[str, list[str]]) -> tuple[dict[str, Any], int]:
        if self.command == "GET" and path in {"/api/status", "/api/health"}:
            return self.app.status(), HTTPStatus.OK
        if self.command == "GET" and path == "/api/settings":
            return self.app.get_settings(), HTTPStatus.OK
        if self.command == "PUT" and path == "/api/settings":
            return self.app.update_settings(self._read_json_body()), HTTPStatus.OK
        if self.command == "GET" and path == "/api/update/status":
            return self.app.get_update_status(), HTTPStatus.OK
        if self.command == "POST" and path == "/api/update/check":
            return self.app.check_for_updates(self._read_json_body()), HTTPStatus.OK
        if self.command == "GET" and path == "/api/securities":
            text = self._bounded_text((query.get("q") or [""])[0], "q")
            try:
                limit = int((query.get("limit") or ["20"])[0])
            except ValueError as exc:
                raise APIError(HTTPStatus.BAD_REQUEST, "limit 必须是整数。") from exc
            return self.app.search_securities(text, min(max(limit, 1), 100)), HTTPStatus.OK
        if self.command == "GET" and path == "/api/rankings/current":
            text = self._bounded_text((query.get("q") or [""])[0], "q")
            sort = self._bounded_text(
                (query.get("sort") or ["short_percent"])[0], "sort"
            ).casefold()
            order = self._bounded_text((query.get("order") or ["desc"])[0], "order").casefold()
            try:
                page = int((query.get("page") or ["1"])[0])
                page_size = int((query.get("page_size") or ["50"])[0])
            except ValueError as exc:
                raise APIError(
                    HTTPStatus.BAD_REQUEST,
                    "page 和 page_size 必须是整数。",
                    code="invalid_pagination",
                ) from exc
            return (
                self.app.get_current_rankings(
                    query=text,
                    sort=sort,
                    order=order,
                    page=page,
                    page_size=page_size,
                ),
                HTTPStatus.OK,
            )
        if self.command == "GET" and path == "/api/price-search":
            text = self._bounded_text((query.get("q") or [""])[0], "q")
            return self.app.price_search(text), HTTPStatus.OK
        if self.command == "POST" and path == "/api/sync":
            body = self._read_json_body()
            force = body.get("force", False)
            if not isinstance(force, bool):
                raise APIError(HTTPStatus.BAD_REQUEST, "force 必须是布尔值。", code="invalid_force")
            started = self.app.sync.start(force=force)
            if not started:
                raise APIError(HTTPStatus.CONFLICT, "已有同步任务正在运行。", code="sync_running")
            return {
                "ok": True,
                "accepted": True,
                "status": "queued",
                "sync": self.app.sync.snapshot(),
            }, HTTPStatus.ACCEPTED

        match = re.fullmatch(r"/api/security/([^/]+)(?:/(short-series|prices|price-symbol))?", path)
        if match:
            identifier = unquote(match.group(1))
            if len(identifier) > MAX_IDENTIFIER_CHARACTERS or any(ord(char) < 32 for char in identifier):
                raise APIError(HTTPStatus.BAD_REQUEST, "证券标识无效。", code="invalid_identifier")
            action = match.group(2)
            if self.command == "GET" and action is None:
                return self.app.get_security(identifier), HTTPStatus.OK
            if self.command == "GET" and action == "short-series":
                return self.app.get_short_series(identifier), HTTPStatus.OK
            if self.command == "GET" and action == "prices":
                refresh = (query.get("refresh") or ["0"])[0].casefold() in {"1", "true", "yes"}
                return self.app.get_prices(identifier, refresh=refresh), HTTPStatus.OK
            if self.command == "POST" and action == "price-symbol":
                return self.app.save_price_symbol(identifier, self._read_json_body()), HTTPStatus.OK
        raise APIError(HTTPStatus.NOT_FOUND, "接口不存在。", code="not_found")

    @staticmethod
    def _bounded_text(value: str, field: str) -> str:
        text = value.strip()
        if len(text) > MAX_QUERY_CHARACTERS or any(ord(char) < 32 for char in text):
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                f"{field} 查询内容无效或过长。",
                code="invalid_query",
            )
        return text

    def _read_json_body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "不支持 Transfer-Encoding；请提供 Content-Length。",
                code="unsupported_transfer_encoding",
            )
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "Content-Length 无效。") from exc
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            raise APIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求正文过大。")
        if length == 0:
            return {}
        if self.headers.get_content_type().casefold() != "application/json":
            raise APIError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "请求正文必须使用 application/json。",
                code="unsupported_media_type",
            )
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "请求正文必须是 UTF-8 JSON。") from exc
        if not isinstance(payload, dict):
            raise APIError(HTTPStatus.BAD_REQUEST, "JSON 正文必须是对象。")
        return payload

    def _send_json(self, payload: Any, *, status: int) -> None:
        body = _json_bytes(payload)
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_static(self, request_path: str, *, head_only: bool) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        if "\x00" in relative or any(ord(char) < 32 for char in relative):
            raise APIError(HTTPStatus.BAD_REQUEST, "静态文件路径无效。", code="invalid_path")
        web_root = self.app.web_dir.resolve()
        candidate = self._confined_static_path(web_root / relative, web_root)
        if candidate.is_dir():
            # Resolve again after appending the directory index.  Otherwise an
            # in-tree directory with an ``index.html`` symlink could escape the
            # web root after the first containment check.
            candidate = self._confined_static_path(candidate / "index.html", web_root)
        if not candidate.is_file():
            # Client-side routes fall back to the application shell, while
            # obvious asset requests retain a real 404.
            if "." not in Path(relative).name:
                candidate = self._confined_static_path(web_root / "index.html", web_root)
            else:
                raise APIError(HTTPStatus.NOT_FOUND, "文件不存在。", code="not_found")
        content_type, _ = mimetypes.guess_type(candidate.name)
        content_type = content_type or "application/octet-stream"
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"} else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    @staticmethod
    def _confined_static_path(candidate: Path, web_root: Path) -> Path:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(web_root)
        except ValueError as exc:
            raise APIError(
                HTTPStatus.FORBIDDEN,
                "禁止访问该路径。",
                code="path_outside_web_root",
            ) from exc
        return resolved

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )


class ShortTrackerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], app: ShortTrackerApplication):
        self.app = app
        # ``HTTPServer`` defaults to AF_INET even when handed ``::1``.  Select
        # the family before ``TCPServer.__init__`` creates its listening socket.
        self.address_family = socket.AF_INET6 if ":" in server_address[0] else socket.AF_INET
        super().__init__(server_address, ShortTrackerRequestHandler)


def serve(
    app: ShortTrackerApplication,
    host: str,
    port: int,
    *,
    shutdown_file: Path | None = None,
    on_started: Callable[[], None] | None = None,
    start_auto_sync: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Short Tracker may only bind to a loopback host")

    stop_watcher = threading.Event()
    with ShortTrackerHTTPServer((host, port), app) as server:
        watcher: threading.Thread | None = None
        if start_auto_sync:
            app.start_background_services()
        if shutdown_file is not None:
            shutdown_file = shutdown_file.resolve()

            def watch_for_shutdown() -> None:
                while not stop_watcher.wait(0.2):
                    if shutdown_file.is_file():
                        force = False
                        try:
                            request = json.loads(shutdown_file.read_text(encoding="utf-8"))
                            force = isinstance(request, dict) and request.get("force") is True
                        except (OSError, UnicodeError, json.JSONDecodeError):
                            # A malformed local request never bypasses the sync
                            # guard.  It may still request a normal shutdown
                            # when no sync is running.
                            force = False
                        if not app.begin_closing(require_idle=not force):
                            # Atomically check-idle-and-close.  A sync cannot
                            # enter between these two operations.
                            shutdown_file.unlink(missing_ok=True)
                            continue
                        # A force request means "begin draining now", never
                        # "terminate a writer".  The watcher remains alive and
                        # the health endpoint reports closing until the current
                        # FCA sync releases both local and process-wide locks.
                        app.drain()
                        # HTTPServer.shutdown() must be called from a thread
                        # other than the one running serve_forever().
                        server.shutdown()
                        return

            watcher = threading.Thread(
                target=watch_for_shutdown,
                name="short-tracker-shutdown-watcher",
                daemon=True,
            )
            watcher.start()

        try:
            if on_started is not None:
                on_started()
            server.serve_forever(poll_interval=0.25)
        finally:
            stop_watcher.set()
            app.begin_closing(require_idle=False)
            app.drain()
            if watcher is not None:
                watcher.join(timeout=1.0)
            if shutdown_file is not None:
                shutdown_file.unlink(missing_ok=True)


__all__ = [
    "APIError",
    "ShortTrackerApplication",
    "ShortTrackerHTTPServer",
    "ShortTrackerRequestHandler",
    "SyncCoordinator",
    "serve",
]
