"""Verify one release version across source, changelog, and optional Git tag."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


VERSION_PATTERN = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)


def source_version(root: Path) -> str:
    source = (root / "short_tracker" / "__init__.py").read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(source)
    if not match:
        raise ValueError("short_tracker.__version__ could not be parsed")
    return match.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--expected")
    parser.add_argument("--tag")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    actual = source_version(root)
    expected = args.expected or actual
    tag_version = args.tag[1:] if args.tag and args.tag.startswith("v") else args.tag
    failures: list[str] = []
    if actual != expected:
        failures.append(f"source version {actual!r} != expected {expected!r}")
    if tag_version and tag_version != expected:
        failures.append(f"tag version {tag_version!r} != expected {expected!r}")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{expected}]" not in changelog:
        failures.append(f"CHANGELOG.md has no section for {expected}")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
