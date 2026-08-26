"""Generate a PyInstaller Windows version resource from the source version."""

from __future__ import annotations

import argparse
from pathlib import Path

from tools.verify_version import source_version


TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple_version},
    prodvers={tuple_version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('FileDescription', 'UK Public Net Short Position Tracker'),
          StringStruct('FileVersion', '{version}.0'),
          StringStruct('InternalName', 'Short Tracker'),
          StringStruct('OriginalFilename', 'Short Tracker.exe'),
          StringStruct('ProductName', 'Short Tracker'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = source_version(args.root.resolve())
    numeric = [int(part) for part in version.split(".")]
    if len(numeric) != 3:
        raise ValueError("Windows release version must contain exactly three numeric components")
    tuple_version = repr(tuple([*numeric, 0]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        TEMPLATE.format(version=version, tuple_version=tuple_version),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
