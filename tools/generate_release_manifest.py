"""Create a machine-readable manifest for a completed release directory."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def git_commit(repo: Path) -> str | None:
    configured = os.environ.get("GITHUB_SHA", "").strip()
    if configured:
        return configured
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--pyinstaller", required=True)
    parser.add_argument("--channel", choices=("preview", "stable"), required=True)
    parser.add_argument("--code-signed", action="store_true")
    args = parser.parse_args()
    release_root = args.release_root.resolve()
    output = args.output.resolve()
    files: list[dict[str, object]] = []
    for path in sorted(release_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file() or path.resolve() == output:
            continue
        relative = path.relative_to(release_root).as_posix()
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
        )
    payload = {
        "schema": 1,
        "product": "Short Tracker",
        "version": args.version,
        "channel": args.channel,
        "target": {
            "os": "windows",
            "architecture": "x86_64",
            "packaging": "onedir",
        },
        "build": {
            "python": platform.python_version(),
            "pyinstaller": args.pyinstaller,
            "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "git_commit": git_commit(args.repo.resolve()),
        },
        "data_included": False,
        "code_signed": bool(args.code_signed),
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
