"""Fail-closed privacy and hygiene audit for the public repository."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        ".build-venv",
        ".pytest_cache",
        "__pycache__",
        "build",
        "dist",
        "release-work",
    }
)
FORBIDDEN_PARTS = frozenset(
    {".cache", "cache", "data", "raw", "runtime", "settings", "webview-profile"}
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".cer",
        ".crt",
        ".csv",
        ".db",
        ".db-journal",
        ".db-shm",
        ".db-wal",
        ".der",
        ".dll",
        ".dmp",
        ".exe",
        ".feather",
        ".har",
        ".jsonl",
        ".key",
        ".log",
        ".mobileprovision",
        ".ndjson",
        ".p12",
        ".parquet",
        ".pdb",
        ".pem",
        ".pdf",
        ".pfx",
        ".pickle",
        ".pkl",
        ".pyc",
        ".pyo",
        ".sqlite",
        ".sqlite-journal",
        ".sqlite-shm",
        ".sqlite-wal",
        ".sqlite3",
        ".tsv",
        ".xls",
        ".xlsx",
        ".zip",
    }
)
RESERVED_WINDOWS_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".config",
        ".css",
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        ".html",
        ".in",
        ".js",
        ".json",
        ".lock",
        ".manifest",
        ".md",
        ".ps1",
        ".py",
        ".spec",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    (
        "Cloudflare token assignment",
        re.compile(
            r"(?i)\b(?:cloudflare|cf)[_-]?(?:api[_-]?)?token\s*[:=]\s*[\"'][A-Za-z0-9_-]{20,}[\"']"
        ),
    ),
    (
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "credential assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|passwd|secret|access[_-]?token)"
            r"\s*[:=]\s*[\"'](?!replace|change|example|your|dummy|test)[^\"'\r\n]{16,}[\"']"
        ),
    ),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Windows user path", re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s<>]+")),
    ("macOS user path", re.compile(r"(?i)(?<![A-Za-z0-9_])/Users/[^/\s<>]+")),
    ("Linux user path", re.compile(r"(?i)(?<![A-Za-z0-9_])/home/[^/\s<>]+")),
    ("file URL", re.compile(r"(?i)\bfile://")),
)


def has_forbidden_file_type(path: Path) -> bool:
    name = path.name.casefold()
    if name == ".coverage" or name.startswith(".coverage."):
        return True
    compound = "".join(path.suffixes[-2:]).casefold()
    return compound in FORBIDDEN_SUFFIXES or path.suffix.casefold() in FORBIDDEN_SUFFIXES


def _tracked_files(root: Path) -> list[Path] | None:
    if not (root / ".git").exists():
        return None
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return [root / item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def _filesystem_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root)
        if any(
            part in SKIPPED_DIRECTORIES or part.casefold().endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if candidate.is_file() or candidate.is_symlink():
            files.append(candidate)
    return files


def audit(root: Path) -> list[str]:
    root = root.resolve()
    files = _tracked_files(root) or _filesystem_files(root)
    failures: list[str] = []
    casefolded: dict[str, str] = {}

    for path in sorted(files, key=lambda item: item.as_posix().casefold()):
        try:
            relative = path.resolve(strict=False).relative_to(root)
        except ValueError:
            failures.append(f"path leaves repository root: {path}")
            continue
        display = relative.as_posix()
        original_relative = path.relative_to(root)

        if path.is_symlink():
            failures.append(f"symbolic links are not allowed: {display}")
            continue
        if any(part.casefold() in FORBIDDEN_PARTS for part in original_relative.parts):
            failures.append(f"forbidden local-data path: {display}")
        if has_forbidden_file_type(path):
            failures.append(f"forbidden generated/private file type: {display}")
        if path.stat().st_size > 5 * 1024 * 1024:
            failures.append(f"unreviewed file larger than 5 MiB: {display}")

        folded = display.casefold()
        previous = casefolded.setdefault(folded, display)
        if previous != display:
            failures.append(f"case-insensitive path collision: {previous} / {display}")

        for part in original_relative.parts:
            stem = part.rstrip(" .").split(".", 1)[0].casefold()
            if part != part.rstrip(" ."):
                failures.append(f"Windows-unsafe trailing character: {display}")
            if stem in RESERVED_WINDOWS_NAMES:
                failures.append(f"Windows-reserved path name: {display}")

        if path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in {
            ".editorconfig",
            ".gitattributes",
            ".gitignore",
            ".python-version",
        }:
            failures.append(f"unreviewed binary or unknown file type: {display}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"cannot read UTF-8 text file {display}: {exc}")
            continue
        for label, pattern in SECRET_PATTERNS:
            if display == "tools/audit_public_tree.py" and label in {
                "Windows user path",
                "macOS user path",
                "Linux user path",
                "file URL",
            }:
                continue
            if pattern.search(content):
                failures.append(f"{label} pattern found in {display}")

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    failures = audit(args.root)
    if failures:
        # A finding can be derived from a secret-bearing file, path, or read
        # error.  Do not echo finding details into CI logs; callers that need
        # local diagnostics can inspect audit()'s return value in-process.
        print(
            "Public-tree audit failed; finding details were suppressed to protect sensitive data.",
            file=sys.stderr,
        )
        return 1
    print("Public-tree audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
