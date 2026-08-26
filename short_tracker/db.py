"""SQLite primitives and migrations for Short Tracker.

The data service deliberately keeps immutable FCA source snapshots and imported
rows side by side.  ``dataset_heads`` is the small, atomic pointer layer which
decides which successful import is visible to readers.  A failed or partial
sync can therefore never replace the last known-good public dataset.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, Sequence


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        r"""
        CREATE TABLE IF NOT EXISTS raw_snapshots (
            id INTEGER PRIMARY KEY,
            source_key TEXT NOT NULL,
            source_url TEXT NOT NULL,
            file_name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            content_type TEXT,
            archive_path TEXT NOT NULL,
            first_retrieved_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL,
            http_last_modified TEXT,
            etag TEXT,
            effective_date TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (source_key, sha256)
        );

        CREATE INDEX IF NOT EXISTS idx_raw_snapshots_source_checked
            ON raw_snapshots(source_key, last_checked_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
            force INTEGER NOT NULL DEFAULT 0 CHECK (force IN (0, 1)),
            details_json TEXT NOT NULL DEFAULT '{}',
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS import_runs (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL,
            snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(id),
            importer_version INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
            row_count INTEGER,
            profile_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            UNIQUE (dataset_key, snapshot_id, importer_version)
        );

        CREATE INDEX IF NOT EXISTS idx_import_runs_dataset_status
            ON import_runs(dataset_key, status, completed_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS dataset_heads (
            dataset_key TEXT PRIMARY KEY,
            snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(id),
            import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
            sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id),
            activated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS issuers (
            id INTEGER PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            canonical_priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_issuers_normalized_name
            ON issuers(normalized_name);

        CREATE TABLE IF NOT EXISTS issuer_identifiers (
            isin TEXT PRIMARY KEY,
            issuer_id INTEGER NOT NULL REFERENCES issuers(id),
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_issuer_identifiers_issuer
            ON issuer_identifiers(issuer_id, isin);

        CREATE TABLE IF NOT EXISTS issuer_aliases (
            id INTEGER PRIMARY KEY,
            issuer_id INTEGER NOT NULL REFERENCES issuers(id),
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            source_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE (issuer_id, name, source_key)
        );

        CREATE INDEX IF NOT EXISTS idx_issuer_aliases_normalized
            ON issuer_aliases(normalized_name, issuer_id);

        CREATE TABLE IF NOT EXISTS legacy_events (
            snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(id),
            row_number INTEGER NOT NULL,
            issuer_id INTEGER NOT NULL REFERENCES issuers(id),
            position_holder TEXT NOT NULL,
            issuer_name TEXT NOT NULL,
            isin TEXT NOT NULL,
            position_bp INTEGER NOT NULL,
            position_date TEXT NOT NULL,
            row_hash TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, row_number)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_legacy_events_replay
            ON legacy_events(snapshot_id, position_date, row_number);

        CREATE INDEX IF NOT EXISTS idx_legacy_events_security_holder
            ON legacy_events(snapshot_id, issuer_id, position_holder, isin, position_date);

        CREATE TABLE IF NOT EXISTS legacy_issuer_aggregate (
            snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(id),
            issuer_id INTEGER NOT NULL REFERENCES issuers(id),
            position_date TEXT NOT NULL,
            aggregate_bp INTEGER NOT NULL CHECK (aggregate_bp >= 0),
            active_holder_count INTEGER NOT NULL CHECK (active_holder_count >= 0),
            event_count INTEGER NOT NULL CHECK (event_count > 0),
            PRIMARY KEY (snapshot_id, issuer_id, position_date)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_legacy_aggregate_series
            ON legacy_issuer_aggregate(snapshot_id, issuer_id, position_date);

        CREATE TABLE IF NOT EXISTS ansp_current (
            snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(id),
            row_number INTEGER NOT NULL,
            issuer_id INTEGER NOT NULL REFERENCES issuers(id),
            company_name TEXT NOT NULL,
            isin TEXT NOT NULL,
            aggregate_bp INTEGER NOT NULL CHECK (aggregate_bp >= 0),
            position_date TEXT NOT NULL,
            row_hash TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, row_number)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_ansp_current_security
            ON ansp_current(snapshot_id, issuer_id, position_date);

        CREATE TABLE IF NOT EXISTS ansp_historic (
            snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(id),
            row_number INTEGER NOT NULL,
            issuer_id INTEGER NOT NULL REFERENCES issuers(id),
            company_name TEXT NOT NULL,
            isin TEXT NOT NULL,
            aggregate_bp INTEGER NOT NULL CHECK (aggregate_bp >= 0),
            position_date TEXT NOT NULL,
            became_historical_date TEXT NOT NULL,
            row_hash TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, row_number)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_ansp_historic_security
            ON ansp_historic(snapshot_id, issuer_id, position_date, became_historical_date);

        CREATE TABLE IF NOT EXISTS rsl_entries (
            snapshot_id INTEGER NOT NULL REFERENCES raw_snapshots(id),
            row_number INTEGER NOT NULL,
            issuer_id INTEGER NOT NULL REFERENCES issuers(id),
            company_name TEXT NOT NULL,
            isin TEXT NOT NULL,
            date_added TEXT,
            share_class TEXT NOT NULL,
            row_hash TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, row_number)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_rsl_entries_security
            ON rsl_entries(snapshot_id, issuer_id, isin);
        """,
    ),
)


class Database:
    """A lightweight connection factory around a migrated SQLite database.

    ``Database`` opens short-lived connections for its context helpers.  This
    is safer for the threaded HTTP server than sharing one SQLite connection.
    Use a real temporary file rather than ``:memory:`` in tests because each
    helper intentionally gets an independent connection.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) == ":memory:":
            raise ValueError("Database(':memory:') is unsupported; use a temporary file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        """Return a configured connection; callers are responsible for closing it."""

        if readonly:
            uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30)
        else:
            conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        if not readonly:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def connection(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection without opening a transaction implicitly."""

        conn = self.connect(readonly=readonly)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a write transaction, rolling it back on any exception."""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        """Apply all schema migrations exactly once."""

        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                )
                """
            )
            applied = {
                row[0]
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query_one(
        self, sql: str, parameters: Sequence[Any] | Mapping[str, Any] = ()
    ) -> sqlite3.Row | None:
        with self.connection(readonly=True) as conn:
            return conn.execute(sql, parameters).fetchone()

    def query_all(
        self, sql: str, parameters: Sequence[Any] | Mapping[str, Any] = ()
    ) -> list[sqlite3.Row]:
        with self.connection(readonly=True) as conn:
            return list(conn.execute(sql, parameters).fetchall())

    def execute(
        self, sql: str, parameters: Sequence[Any] | Mapping[str, Any] = ()
    ) -> int:
        """Execute one statement and return its ``lastrowid``."""

        with self.transaction() as conn:
            cursor = conn.execute(sql, parameters)
            return int(cursor.lastrowid or 0)

    @property
    def schema_version(self) -> int:
        row = self.query_one("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations")
        return int(row["version"]) if row else 0
