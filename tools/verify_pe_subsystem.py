"""Verify that Windows PE files use the GUI (not console) subsystem."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys


def subsystem(path: Path) -> int:
    with path.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("missing MZ header")
        handle.seek(0x3C)
        pe_offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(pe_offset)
        if handle.read(4) != b"PE\0\0":
            raise ValueError("missing PE signature")
        handle.seek(pe_offset + 24)
        magic = struct.unpack("<H", handle.read(2))[0]
        if magic not in {0x10B, 0x20B}:
            raise ValueError(f"unsupported optional-header magic 0x{magic:04x}")
        handle.seek(pe_offset + 24 + 68)
        return struct.unpack("<H", handle.read(2))[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    failures: list[str] = []
    for path in args.files:
        try:
            value = subsystem(path)
        except (OSError, ValueError, struct.error) as exc:
            failures.append(f"{path}: {exc}")
            continue
        if value != 2:
            failures.append(f"{path}: subsystem {value}, expected 2 (Windows GUI)")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("PE subsystem verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
