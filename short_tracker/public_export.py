"""Deterministic, fail-closed export of FCA-only data for the public website.

The desktop application owns a mutable SQLite database and several local-only
features.  A public website must never receive that database, price-provider
caches, user settings, or write-capable API state.  This module therefore reads
an existing database through SQLite's read-only URI mode and emits a narrowly
allowlisted static JSON contract.

The public grain is one reportable share/ISIN.  This is important for issuers
with more than one share class: current FCA ANSP rows and their histories are
not summed across ISINs.  The pre-July-2026 named disclosures are replayed for
the selected ISIN, but position-holder identities are used only in memory and
are never exported.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

from .fca import ANSP_REGIME_START, LEGACY_PUBLIC_THRESHOLD_BP


PUBLIC_EXPORT_SCHEMA_VERSION = 1
PUBLIC_EXPORT_FORMAT = "ukshort-fca-static-json"
REQUIRED_DATASETS = (
    "legacy_named",
    "ansp_current",
    "ansp_historic",
    "reportable_shares",
)
_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_PATH_PATTERN = re.compile(r"(?i)(?:^|[\s\"'])(?:[a-z]:[\\/]|/(?:home|users)/)")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FORBIDDEN_PUBLIC_KEY_PARTS = (
    "archive_path",
    "cache",
    "database",
    "local_path",
    "position_holder",
    "price",
    "ticker",
    "yahoo",
)


class PublicExportError(RuntimeError):
    """Raised when source state cannot be safely published."""


@dataclass(frozen=True)
class PublicExportResult:
    """Small serialisable summary returned after a successful directory swap."""

    output_directory: str
    schema_version: int
    file_count: int
    byte_size: int
    security_count: int
    ranking_count: int
    source_state_at: str
    manifest_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ReadOnlyDatabase:
    """Minimal query adapter that can never migrate or write the source DB."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise PublicExportError(f"database does not exist: {self.path}")

    def connect(self) -> sqlite3.Connection:
        uri = f"{self.path.as_uri()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=30)
        except sqlite3.Error as exc:
            raise PublicExportError(f"cannot open database read-only: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_json(root: Path, relative_path: str, value: Any) -> None:
    destination = root.joinpath(*relative_path.split("/"))
    try:
        destination.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise PublicExportError(f"output path leaves staging root: {relative_path}") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_canonical_json_bytes(value))


def _clean_text(value: Any, *, label: str) -> str:
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    if not text:
        raise PublicExportError(f"blank {label}")
    if _CONTROL_CHARACTER_PATTERN.search(text):
        raise PublicExportError(f"control character in {label}")
    return text


def _validate_isin(value: Any) -> str:
    isin = _clean_text(value, label="ISIN").upper()
    if not _ISIN_PATTERN.fullmatch(isin):
        raise PublicExportError(f"unsafe or malformed ISIN: {isin!r}")
    return isin


def _validate_date(value: Any, *, label: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    text = _clean_text(value, label=label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise PublicExportError(f"invalid {label}: {text!r}") from exc
    return parsed.isoformat()


def _percent_from_bp(value: int) -> float:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicExportError(f"invalid non-negative basis-point value: {value!r}")
    return value / 100.0


def _validate_fca_url(value: Any) -> str:
    url = _clean_text(value, label="FCA source URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"fca.org.uk", "www.fca.org.uk"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PublicExportError(f"non-FCA source URL cannot be published: {url!r}")
    return url


def _required_tables(conn: sqlite3.Connection) -> None:
    required = {
        "ansp_current",
        "ansp_historic",
        "dataset_heads",
        "import_runs",
        "issuer_identifiers",
        "issuers",
        "legacy_events",
        "raw_snapshots",
        "rsl_entries",
    }
    existing = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing = sorted(required - existing)
    if missing:
        raise PublicExportError(f"database schema is incomplete: {', '.join(missing)}")


def _load_coverage(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT h.dataset_key, h.activated_at,
               s.source_key, s.source_url, s.file_name, s.sha256,
               s.byte_size, s.last_checked_at, s.effective_date,
               i.row_count, i.completed_at AS imported_at
          FROM dataset_heads h
          JOIN raw_snapshots s ON s.id = h.snapshot_id
          JOIN import_runs i ON i.id = h.import_run_id
         WHERE h.dataset_key IN (?, ?, ?, ?)
        """,
        REQUIRED_DATASETS,
    ).fetchall()
    coverage: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["dataset_key"])
        sha256 = _clean_text(row["sha256"], label=f"{key} SHA-256").casefold()
        if not _SHA256_PATTERN.fullmatch(sha256):
            raise PublicExportError(f"invalid source SHA-256 for {key}")
        file_name = _clean_text(row["file_name"], label=f"{key} file name")
        if Path(file_name).name != file_name or "/" in file_name or "\\" in file_name:
            raise PublicExportError(f"unsafe source file name for {key}")
        row_count = int(row["row_count"] or 0)
        if row_count <= 0:
            raise PublicExportError(f"active dataset is empty: {key}")
        coverage[key] = {
            "dataset": key,
            "source_key": _clean_text(row["source_key"], label=f"{key} source key"),
            "source_url": _validate_fca_url(row["source_url"]),
            "source_file": file_name,
            "source_sha256": sha256,
            "source_byte_size": int(row["byte_size"]),
            "source_effective_date": _validate_date(
                row["effective_date"], label=f"{key} effective date", allow_none=True
            ),
            "last_checked_at": _clean_text(
                row["last_checked_at"], label=f"{key} last checked timestamp"
            ),
            "imported_at": _clean_text(
                row["imported_at"], label=f"{key} import timestamp"
            ),
            "activated_at": _clean_text(
                row["activated_at"], label=f"{key} activation timestamp"
            ),
            "row_count": row_count,
        }
    missing = [key for key in REQUIRED_DATASETS if key not in coverage]
    if missing:
        raise PublicExportError(
            f"required active FCA datasets are missing: {', '.join(missing)}"
        )
    return {key: coverage[key] for key in REQUIRED_DATASETS}


def _active_heads(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT dataset_key, snapshot_id FROM dataset_heads WHERE dataset_key IN (?, ?, ?, ?)",
        REQUIRED_DATASETS,
    )
    heads = {str(row["dataset_key"]): int(row["snapshot_id"]) for row in rows}
    missing = [key for key in REQUIRED_DATASETS if key not in heads]
    if missing:
        raise PublicExportError(f"missing active snapshots: {', '.join(missing)}")
    return heads


def _all_active_isins(
    conn: sqlite3.Connection, heads: Mapping[str, int]
) -> list[str]:
    rows = conn.execute(
        """
        SELECT isin FROM legacy_events WHERE snapshot_id = ?
        UNION
        SELECT isin FROM ansp_current WHERE snapshot_id = ?
        UNION
        SELECT isin FROM ansp_historic WHERE snapshot_id = ?
        UNION
        SELECT isin FROM rsl_entries WHERE snapshot_id = ?
        """,
        (
            heads["legacy_named"],
            heads["ansp_current"],
            heads["ansp_historic"],
            heads["reportable_shares"],
        ),
    ).fetchall()
    isins = sorted({_validate_isin(row["isin"]) for row in rows})
    if not isins:
        raise PublicExportError("no ISINs exist in the active FCA datasets")
    return isins


def _security_identity(
    conn: sqlite3.Connection, heads: Mapping[str, int], isin: str
) -> dict[str, Any]:
    issuer_rows = conn.execute(
        "SELECT issuer_id FROM issuer_identifiers WHERE isin = ?", (isin,)
    ).fetchall()
    if len(issuer_rows) != 1:
        raise PublicExportError(f"ISIN does not resolve to exactly one issuer: {isin}")
    issuer_id = int(issuer_rows[0]["issuer_id"])
    issuer = conn.execute(
        "SELECT canonical_name FROM issuers WHERE id = ?", (issuer_id,)
    ).fetchone()
    if issuer is None:
        raise PublicExportError(f"issuer identity is missing for {isin}")

    name_rows = conn.execute(
        """
        SELECT 0 AS priority, company_name AS name, position_date AS observed_date,
               row_number
          FROM ansp_current WHERE snapshot_id = ? AND isin = ?
        UNION ALL
        SELECT 1, company_name, COALESCE(date_added, ''), row_number
          FROM rsl_entries WHERE snapshot_id = ? AND isin = ?
        UNION ALL
        SELECT 2, company_name, position_date, row_number
          FROM ansp_historic WHERE snapshot_id = ? AND isin = ?
        UNION ALL
        SELECT 3, issuer_name, position_date, row_number
          FROM legacy_events WHERE snapshot_id = ? AND isin = ?
        ORDER BY priority ASC, observed_date DESC, row_number DESC
        """,
        (
            heads["ansp_current"],
            isin,
            heads["reportable_shares"],
            isin,
            heads["ansp_historic"],
            isin,
            heads["legacy_named"],
            isin,
        ),
    ).fetchall()
    aliases = sorted(
        {
            _clean_text(row["name"], label=f"company name for {isin}")
            for row in name_rows
        },
        key=lambda item: (item.casefold(), item),
    )
    canonical = _clean_text(issuer["canonical_name"], label=f"canonical name for {isin}")
    if canonical not in aliases:
        aliases.append(canonical)
        aliases.sort(key=lambda item: (item.casefold(), item))
    name = (
        _clean_text(name_rows[0]["name"], label=f"display name for {isin}")
        if name_rows
        else canonical
    )

    reportable_rows = conn.execute(
        """
        SELECT company_name, date_added, share_class
          FROM rsl_entries
         WHERE snapshot_id = ? AND isin = ?
         ORDER BY row_number ASC
        """,
        (heads["reportable_shares"], isin),
    ).fetchall()
    reportable_shares = [
        {
            "company_name": _clean_text(
                row["company_name"], label=f"RSL company name for {isin}"
            ),
            "date_added": _validate_date(
                row["date_added"], label=f"RSL date added for {isin}", allow_none=True
            ),
            "share_class": _clean_text(
                row["share_class"], label=f"RSL share class for {isin}"
            ),
        }
        for row in reportable_rows
    ]
    return {
        "name": name,
        "isin": isin,
        "aliases": aliases,
        "reportable": bool(reportable_shares),
        "reportable_shares": reportable_shares,
    }


def _legacy_series(
    conn: sqlite3.Connection, snapshot_id: int, isin: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT position_date, row_number, position_holder, position_bp
          FROM legacy_events
         WHERE snapshot_id = ? AND isin = ?
         ORDER BY position_date ASC, row_number ASC
        """,
        (snapshot_id, isin),
    ).fetchall()
    state: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(rows):
        position_date = _validate_date(
            rows[index]["position_date"], label=f"legacy position date for {isin}"
        )
        event_count = 0
        while index < len(rows) and rows[index]["position_date"] == position_date:
            row = rows[index]
            holder = _clean_text(row["position_holder"], label="legacy holder identity")
            bp = int(row["position_bp"])
            if bp >= LEGACY_PUBLIC_THRESHOLD_BP:
                state[holder] = bp
            else:
                state.pop(holder, None)
            event_count += 1
            index += 1
        aggregate_bp = sum(state.values())
        result.append(
            {
                "date": position_date,
                "position_date": position_date,
                "value_bp": aggregate_bp,
                "value_percent": _percent_from_bp(aggregate_bp),
                "active_disclosed_holder_count": len(state),
                "source_event_count": event_count,
                "regime": "legacy_named_public_disclosures_reconstructed",
            }
        )
    return result


def _ansp_series(
    conn: sqlite3.Connection, heads: Mapping[str, int], isin: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    historic_rows = conn.execute(
        """
        SELECT position_date, became_historical_date, aggregate_bp, row_number
          FROM ansp_historic
         WHERE snapshot_id = ? AND isin = ?
         ORDER BY MAX(position_date, ?) ASC,
                  became_historical_date ASC, row_number ASC
        """,
        (heads["ansp_historic"], isin, ANSP_REGIME_START),
    ).fetchall()
    points: list[dict[str, Any]] = []
    for row in historic_rows:
        position_date = _validate_date(
            row["position_date"], label=f"ANSP position date for {isin}"
        )
        became_historical_date = _validate_date(
            row["became_historical_date"],
            label=f"ANSP historical date for {isin}",
        )
        aggregate_bp = int(row["aggregate_bp"])
        points.append(
            {
                "date": max(position_date, ANSP_REGIME_START),
                "position_date": position_date,
                "became_historical_date": became_historical_date,
                "value_bp": aggregate_bp,
                "value_percent": _percent_from_bp(aggregate_bp),
                "is_current": False,
                "transition_date_clamped": position_date < ANSP_REGIME_START,
                "regime": "anonymous_fca_ansp",
            }
        )

    current_rows = conn.execute(
        """
        SELECT position_date, aggregate_bp, row_number
          FROM ansp_current
         WHERE snapshot_id = ? AND isin = ?
         ORDER BY position_date DESC, row_number DESC
        """,
        (heads["ansp_current"], isin),
    ).fetchall()
    if len(current_rows) > 1:
        raise PublicExportError(f"current ANSP contains duplicate ISIN rows: {isin}")
    current: dict[str, Any] | None = None
    if current_rows:
        row = current_rows[0]
        position_date = _validate_date(
            row["position_date"], label=f"current ANSP position date for {isin}"
        )
        aggregate_bp = int(row["aggregate_bp"])
        current = {
            "date": max(position_date, ANSP_REGIME_START),
            "position_date": position_date,
            "value_bp": aggregate_bp,
            "value_percent": _percent_from_bp(aggregate_bp),
            "is_current": True,
            "transition_date_clamped": position_date < ANSP_REGIME_START,
            "regime": "anonymous_fca_ansp",
        }
        points.append(dict(current))
    return points, current


def _series_payload(
    conn: sqlite3.Connection,
    heads: Mapping[str, int],
    coverage: Mapping[str, Mapping[str, Any]],
    isin: str,
) -> dict[str, Any]:
    security = _security_identity(conn, heads, isin)
    legacy = _legacy_series(conn, heads["legacy_named"], isin)
    ansp, current = _ansp_series(conn, heads, isin)
    series_coverage = {
        key: {
            "dataset": key,
            "source_sha256": coverage[key]["source_sha256"],
            "source_effective_date": coverage[key]["source_effective_date"],
        }
        for key in REQUIRED_DATASETS
    }
    return {
        "schema_version": PUBLIC_EXPORT_SCHEMA_VERSION,
        "security": security,
        "legacy": legacy,
        "ansp": ansp,
        "current": current,
        # Full source metadata lives once in status.json.  Per-security files
        # retain the exact source hashes without repeating URLs and timestamps
        # thousands of times.
        "coverage": series_coverage,
        "methodology": {
            "grain": "one reportable share identified by exact ISIN",
            "unit": "percentage points of issued share capital",
            "legacy": (
                "Reconstructed for this exact ISIN from the published named "
                "position-holder state. Values at or above 0.50% contribute; "
                "a later below-threshold or closure row removes that holder."
            ),
            "ansp": (
                "Official FCA anonymous aggregate for this exact ISIN. The "
                "legacy reconstruction and ANSP series remain separate."
            ),
            "caveats": [
                "Neither series is total market short interest.",
                "Sub-threshold, exempt, and otherwise undisclosed positions are absent.",
                "FCA may revise history after late, corrected, or verified notifications.",
            ],
        },
    }


def _ranking_payload(
    conn: sqlite3.Connection, heads: Mapping[str, int], coverage: Mapping[str, Any]
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT company_name, isin, aggregate_bp, position_date, row_number
          FROM ansp_current
         WHERE snapshot_id = ?
         ORDER BY aggregate_bp DESC,
                  company_name COLLATE NOCASE ASC,
                  company_name ASC, isin ASC, row_number ASC
        """,
        (heads["ansp_current"],),
    ).fetchall()
    if not rows:
        raise PublicExportError("current FCA ranking is empty")
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        isin = _validate_isin(row["isin"])
        if isin in seen:
            raise PublicExportError(f"current ranking contains duplicate ISIN: {isin}")
        seen.add(isin)
        aggregate_bp = int(row["aggregate_bp"])
        items.append(
            {
                "rank": rank,
                "company_name": _clean_text(
                    row["company_name"], label=f"ranking company name for {isin}"
                ),
                "isin": isin,
                "aggregate_bp": aggregate_bp,
                "short_percent": _percent_from_bp(aggregate_bp),
                "position_date": _validate_date(
                    row["position_date"], label=f"ranking position date for {isin}"
                ),
                "series_path": f"securities/{isin}/short-series.json",
            }
        )
    return {
        "schema_version": PUBLIC_EXPORT_SCHEMA_VERSION,
        "as_of_date": max(item["position_date"] for item in items),
        "count": len(items),
        "items": items,
        "coverage": coverage["ansp_current"],
        "methodology": {
            "grain": "one FCA current ANSP row per reportable share/ISIN",
            "order": "aggregate_bp descending, then company name, ISIN, and source row",
            "aggregation_across_isins": False,
            "is_total_market_short_interest": False,
        },
    }


def _index_payload(
    series_by_isin: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for isin, payload in series_by_isin.items():
        security = payload["security"]
        current = payload["current"]
        items.append(
            {
                "name": security["name"],
                "isin": isin,
                "aliases": security["aliases"],
                "reportable": security["reportable"],
                "current_short_percent": (
                    current["value_percent"] if current is not None else None
                ),
                "current_position_date": (
                    current["position_date"] if current is not None else None
                ),
                "series_path": f"securities/{isin}/short-series.json",
            }
        )
    items.sort(key=lambda item: (item["name"].casefold(), item["name"], item["isin"]))
    return {
        "schema_version": PUBLIC_EXPORT_SCHEMA_VERSION,
        "count": len(items),
        "items": items,
    }


def _scan_public_value(value: Any, *, location: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PublicExportError(f"non-string JSON key at {location}")
            folded = key.casefold()
            if any(part in folded for part in _FORBIDDEN_PUBLIC_KEY_PARTS):
                raise PublicExportError(f"forbidden public key at {location}.{key}")
            _scan_public_value(child, location=f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public_value(child, location=f"{location}[{index}]")
        return
    if isinstance(value, str):
        if _CONTROL_CHARACTER_PATTERN.search(value):
            raise PublicExportError(f"control character in public string at {location}")
        if _LOCAL_PATH_PATTERN.search(value) or ".sqlite" in value.casefold():
            raise PublicExportError(f"local filesystem detail at {location}")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise PublicExportError(f"unsupported public JSON type at {location}")


def _file_inventory(root: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        body = path.read_bytes()
        inventory.append(
            {
                "path": relative,
                "byte_size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "content_type": "application/json; charset=utf-8",
            }
        )
    return inventory


def _validate_staging(root: Path, database_path: Path) -> tuple[int, int]:
    if any(path.is_symlink() for path in root.rglob("*")):
        raise PublicExportError("symbolic links are forbidden in the public export")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files or not (root / "manifest.json").is_file():
        raise PublicExportError("public export is missing its manifest")
    database_text = str(database_path.resolve()).casefold()
    for path in files:
        if path.suffix != ".json":
            raise PublicExportError(f"unexpected public artifact type: {path.name}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PublicExportError(f"invalid JSON artifact: {path.name}") from exc
        _scan_public_value(value, location=path.relative_to(root).as_posix())
        if database_text and database_text in path.read_text(encoding="utf-8").casefold():
            raise PublicExportError(f"database path leaked into {path.name}")

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected = _file_inventory(root)
    if manifest.get("files") != expected:
        raise PublicExportError("manifest file inventory does not match staged artifacts")
    if manifest.get("content_file_count") != len(expected):
        raise PublicExportError("manifest content file count is incorrect")
    if manifest.get("file_count") != len(expected) + 1:
        raise PublicExportError("manifest file count is incorrect")
    return len(files), sum(path.stat().st_size for path in files)


def _safe_output_paths(database_path: Path, output_directory: Path) -> tuple[Path, Path]:
    database = database_path.expanduser().resolve()
    output = output_directory.expanduser().resolve(strict=False)
    if output == output.parent:
        raise PublicExportError("filesystem root cannot be an export directory")
    try:
        database.relative_to(output)
    except ValueError:
        pass
    else:
        raise PublicExportError("export directory cannot contain the source database")
    try:
        output.relative_to(database.parent)
    except ValueError:
        pass
    else:
        raise PublicExportError("export directory cannot be inside the source data directory")
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise PublicExportError("existing export target must be a normal directory")
    return database, output


def _replace_directory(staging: Path, output: Path) -> None:
    backup: Path | None = None
    try:
        if output.exists():
            backup = output.parent / f".{output.name}-previous-{os.getpid()}"
            if backup.exists():
                raise PublicExportError(f"temporary backup path already exists: {backup}")
            os.replace(output, backup)
        os.replace(staging, output)
    except Exception:
        if backup is not None and backup.exists() and not output.exists():
            os.replace(backup, output)
        raise
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


def export_public_data(
    database_path: str | Path, output_directory: str | Path
) -> PublicExportResult:
    """Build and atomically install one complete FCA-only static export.

    Re-running this function against unchanged source state produces identical
    bytes.  ``source_state_at`` is derived from active snapshot metadata rather
    than the wall clock, so a rebuild alone cannot create a noisy diff.
    """

    database, output = _safe_output_paths(Path(database_path), Path(output_directory))
    readonly = _ReadOnlyDatabase(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}-staging-", dir=output.parent)
    ).resolve()
    try:
        with readonly.connection() as conn:
            _required_tables(conn)
            coverage = _load_coverage(conn)
            heads = _active_heads(conn)
            isins = _all_active_isins(conn, heads)
            series_by_isin = {
                isin: _series_payload(conn, heads, coverage, isin) for isin in isins
            }
            ranking = _ranking_payload(conn, heads, coverage)

        index = _index_payload(series_by_isin)
        source_state_at = max(
            timestamp
            for item in coverage.values()
            for timestamp in (
                item["last_checked_at"],
                item["imported_at"],
                item["activated_at"],
            )
        )
        for isin, payload in series_by_isin.items():
            _write_json(staging, f"securities/{isin}/short-series.json", payload)
        _write_json(staging, "rankings-current.json", ranking)
        _write_json(staging, "securities-index.json", index)
        _write_json(
            staging,
            "status.json",
            {
                "schema_version": PUBLIC_EXPORT_SCHEMA_VERSION,
                "state": "ready",
                "data_mode": "fca_only",
                "source_authority": "UK Financial Conduct Authority",
                "source_state_at": source_state_at,
                "current_rankings_as_of": ranking["as_of_date"],
                "security_count": index["count"],
                "ranking_count": ranking["count"],
                "coverage": coverage,
                "scope": {
                    "included": ["FCA public net short position disclosures"],
                    "excluded": [
                        "market-price provider data",
                        "provider caches",
                        "local user settings",
                        "legacy position-holder identities",
                    ],
                },
            },
        )
        inventory = _file_inventory(staging)
        _write_json(
            staging,
            "manifest.json",
            {
                "schema_version": PUBLIC_EXPORT_SCHEMA_VERSION,
                "format": PUBLIC_EXPORT_FORMAT,
                "data_mode": "fca_only",
                "source_state_at": source_state_at,
                "security_count": index["count"],
                "ranking_count": ranking["count"],
                "file_count": len(inventory) + 1,
                "content_file_count": len(inventory),
                "entrypoints": {
                    "status": "status.json",
                    "current_rankings": "rankings-current.json",
                    "securities_index": "securities-index.json",
                },
                "files": inventory,
            },
        )
        file_count, byte_size = _validate_staging(staging, database)
        manifest_sha256 = hashlib.sha256((staging / "manifest.json").read_bytes()).hexdigest()
        _replace_directory(staging, output)
        return PublicExportResult(
            output_directory=str(output),
            schema_version=PUBLIC_EXPORT_SCHEMA_VERSION,
            file_count=file_count,
            byte_size=byte_size,
            security_count=index["count"],
            ranking_count=ranking["count"],
            source_state_at=source_state_at,
            manifest_sha256=manifest_sha256,
        )
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging)
        if isinstance(exc, PublicExportError):
            raise
        if isinstance(exc, sqlite3.Error):
            raise PublicExportError(f"SQLite export query failed: {exc}") from exc
        raise
