"""Command-line entry point for the local UK Short Tracker."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid
import webbrowser

from .server import ShortTrackerApplication, serve
from .fca import FCA_SOURCES
from .paths import default_data_dir, resource_root


PROJECT_ROOT = resource_root()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m short_tracker",
        description="Local, read-only UK public short-position research dashboard.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir(PROJECT_ROOT),
        help=(
            "writable data directory (source default: PROJECT_ROOT/data; "
            "Windows app default: %%LOCALAPPDATA%%/ShortTracker/data)"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run the local dashboard")
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        choices=("127.0.0.1", "localhost", "::1"),
    )
    serve_parser.add_argument("--port", type=int, default=8777)
    serve_parser.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    serve_parser.add_argument("--skip-startup-sync", action="store_true")
    serve_parser.add_argument(
        "--shutdown-token",
        help=argparse.SUPPRESS,
    )
    serve_parser.add_argument(
        "--instance-id",
        help=argparse.SUPPRESS,
    )

    sync_parser = subparsers.add_parser("sync", help="synchronise official FCA datasets")
    sync_parser.add_argument("--force", action="store_true")

    subparsers.add_parser("verify", help="verify the active local database")
    return parser


def _verification_report(app: ShortTrackerApplication) -> dict[str, object]:
    checks: list[dict[str, object]] = []

    def add(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    add("schema_version", app.db.schema_version >= 1, app.db.schema_version)
    heads = app.db.query_all("SELECT dataset_key FROM dataset_heads ORDER BY dataset_key")
    head_names = [row["dataset_key"] for row in heads]
    required = {"legacy_named", "ansp_current", "ansp_historic", "reportable_shares"}
    add("four_active_datasets", required.issubset(set(head_names)), head_names)

    head_consistency = app.db.query_one(
        """
        SELECT COUNT(*) AS count
          FROM dataset_heads h
          JOIN import_runs i ON i.id = h.import_run_id
          JOIN sync_runs r ON r.id = h.sync_run_id
         WHERE i.status != 'success'
            OR i.completed_at IS NULL
            OR i.row_count IS NULL
            OR i.snapshot_id != h.snapshot_id
            OR i.dataset_key != h.dataset_key
            OR r.status != 'success'
            OR r.completed_at IS NULL
        """
    )
    inconsistent_heads = int(head_consistency["count"] or 0) if head_consistency else 0
    add("active_head_import_links", inconsistent_heads == 0, inconsistent_heads)

    integrity = app.db.query_one("PRAGMA integrity_check")
    integrity_value = integrity[0] if integrity else "missing"
    add("sqlite_integrity", integrity_value == "ok", integrity_value)

    foreign_keys = app.db.query_all("PRAGMA foreign_key_check")
    add("foreign_keys", not foreign_keys, len(foreign_keys))

    expected_tables = {
        "legacy_named": "legacy_events",
        "ansp_current": "ansp_current",
        "ansp_historic": "ansp_historic",
        "reportable_shares": "rsl_entries",
    }
    for dataset, table in expected_tables.items():
        row = app.db.query_one(
            f"""
            SELECT COUNT(*) AS count, MAX(i.row_count) AS imported_count
              FROM {table} t
              JOIN dataset_heads h ON h.dataset_key = ? AND h.snapshot_id = t.snapshot_id
              JOIN import_runs i ON i.id = h.import_run_id
            """,
            (dataset,),
        )
        count = int(row["count"] or 0) if row else 0
        imported_count = int(row["imported_count"] or 0) if row else 0
        add(
            f"active_rows_{dataset}",
            count == imported_count,
            {"table_rows": count, "import_run_rows": imported_count},
        )

    orphan_aggregate = app.db.query_one(
        """
        SELECT COUNT(*) AS count
          FROM legacy_issuer_aggregate a
          LEFT JOIN issuers i ON i.id = a.issuer_id
         WHERE i.id IS NULL
        """
    )
    orphan_count = int(orphan_aggregate["count"] or 0) if orphan_aggregate else 0
    add("legacy_aggregate_issuer_links", orphan_count == 0, orphan_count)

    aggregate_profile = app.db.query_one(
        """
        SELECT i.profile_json, h.snapshot_id
          FROM dataset_heads h
          JOIN import_runs i ON i.id = h.import_run_id
         WHERE h.dataset_key = 'legacy_named'
        """
    )
    if aggregate_profile:
        try:
            profile = json.loads(aggregate_profile["profile_json"] or "{}")
        except json.JSONDecodeError:
            profile = {}
        aggregate_row = app.db.query_one(
            "SELECT COUNT(*) AS count FROM legacy_issuer_aggregate WHERE snapshot_id = ?",
            (aggregate_profile["snapshot_id"],),
        )
        actual_aggregate_rows = int(aggregate_row["count"] or 0) if aggregate_row else 0
        expected_aggregate_rows = profile.get("derived_aggregate_row_count")
        add(
            "legacy_derived_profile",
            isinstance(expected_aggregate_rows, int)
            and actual_aggregate_rows == expected_aggregate_rows,
            {
                "table_rows": actual_aggregate_rows,
                "profile_rows": expected_aggregate_rows,
            },
        )
    else:
        add("legacy_derived_profile", False, "missing legacy import profile")

    archive_results: dict[str, object] = {}
    archive_ok = True
    data_root = app.data_dir.resolve()
    for source in FCA_SOURCES:
        snapshot = app.db.query_one(
            """
            SELECT id, archive_path, sha256, byte_size, last_checked_at
              FROM raw_snapshots
             WHERE source_key = ?
             ORDER BY last_checked_at DESC, id DESC LIMIT 1
            """,
            (source.key,),
        )
        if snapshot is None:
            archive_ok = False
            archive_results[source.key] = "missing database snapshot"
            continue
        candidate = (data_root / snapshot["archive_path"]).resolve()
        try:
            candidate.relative_to(data_root)
        except ValueError:
            archive_ok = False
            archive_results[source.key] = "archive path leaves data directory"
            continue
        if not candidate.is_file():
            archive_ok = False
            archive_results[source.key] = "archive file missing"
            continue
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_size = candidate.stat().st_size
        matches = (
            actual_size == int(snapshot["byte_size"])
            and digest.hexdigest() == snapshot["sha256"]
        )
        archive_ok = archive_ok and matches
        archive_results[source.key] = {
            "matches": matches,
            "bytes": actual_size,
            "last_checked_at": snapshot["last_checked_at"],
        }
    add("latest_official_archives", archive_ok, archive_results)

    required_web_assets = ("index.html", "styles.css", "i18n.js", "app.js")
    web_assets = {
        name: (app.web_dir / name).is_file() and (app.web_dir / name).stat().st_size > 0
        for name in required_web_assets
    }
    add("web_assets", all(web_assets.values()), web_assets)

    latest_sync = app.db.query_one(
        "SELECT id, status, completed_at, error FROM sync_runs ORDER BY id DESC LIMIT 1"
    )
    add(
        "latest_sync_success",
        bool(latest_sync and latest_sync["status"] == "success"),
        dict(latest_sync) if latest_sync else None,
    )
    passed = all(bool(check["passed"]) for check in checks)
    return {
        "ok": passed,
        "database": str(app.db.path),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "serve" and not 1024 <= args.port <= 65535:
        print("port must be between 1024 and 65535", file=sys.stderr)
        return 2

    instance_id: str | None = None
    shutdown_token: str | None = None
    if args.command == "serve":
        if args.instance_id:
            try:
                instance_id = str(uuid.UUID(args.instance_id))
            except (ValueError, AttributeError):
                print("instance id must be a UUID", file=sys.stderr)
                return 2
        if args.shutdown_token:
            try:
                shutdown_token = str(uuid.UUID(args.shutdown_token))
            except (ValueError, AttributeError):
                print("shutdown token must be a UUID", file=sys.stderr)
                return 2

    app = ShortTrackerApplication.create(
        PROJECT_ROOT,
        data_dir=args.data_dir,
        instance_id=instance_id,
    )

    if args.command == "sync":
        try:
            result = app.sync.run(force=args.force)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify":
        report = _verification_report(app)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if args.command == "serve":
        display_host = f"[{args.host}]" if ":" in args.host else args.host
        url = f"http://{display_host}:{args.port}/"
        shutdown_file: Path | None = None
        if shutdown_token:
            runtime_dir = app.data_dir / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            shutdown_file = runtime_dir / f"stop-{shutdown_token}.request"
        if args.open:
            import threading

            browser_timer = threading.Timer(0.7, lambda: webbrowser.open(url))
            browser_timer.daemon = True
            browser_timer.start()
        print(f"UK Short Tracker: {url}")
        print(f"Portable data directory: {app.data_dir}")
        print("Research-only local service. Press Ctrl+C to stop.")
        try:
            serve(
                app,
                args.host,
                args.port,
                shutdown_file=shutdown_file,
                start_auto_sync=not args.skip_startup_sync,
            )
        except KeyboardInterrupt:
            print("\nShort Tracker stopped.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
