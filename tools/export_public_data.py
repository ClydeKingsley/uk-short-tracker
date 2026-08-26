"""Build the deterministic FCA-only JSON bundle used by the public website."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from short_tracker.public_export import PublicExportError, export_public_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="Existing Short Tracker SQLite database (opened read-only).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Static JSON directory to replace after complete validation.",
    )
    args = parser.parse_args(argv)
    try:
        result = export_public_data(args.database, args.output)
    except PublicExportError as exc:
        print(f"Public data export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
