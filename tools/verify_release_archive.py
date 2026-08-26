"""Verify paths, privacy boundaries, and manifest hashes in a release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import PurePosixPath
import sys
from typing import Mapping
import zipfile


FORBIDDEN_SUFFIXES = (
    ".cer",
    ".crt",
    ".csv",
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".der",
    ".dmp",
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
    ".sqlite",
    ".sqlite-journal",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".tsv",
    ".xls",
    ".xlsx",
)
FORBIDDEN_PARTS = frozenset(
    {
        ".cache",
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "cache",
        "data",
        "dist",
        "raw",
        "release-work",
        "settings",
        "tests",
        "webview-profile",
    }
)
ALLOWED_DEPENDENCY_RUNTIME_PARENT = ("_internal", "pythonnet", "runtime")
REQUIRED_THIRD_PARTY_COMPONENTS = frozenset(
    {
        "bottle",
        "cffi",
        "clr-loader",
        "dotnet-standard",
        "et-xmlfile",
        "microsoft-webview2",
        "libffi",
        "openpyxl",
        "openssl",
        "packaging",
        "proxy-tools",
        "pycparser",
        "pyinstaller-bootloader",
        "python",
        "pythonnet",
        "pywebview",
        "setuptools",
        "sqlite",
        "typing-extensions",
    }
)


def is_unreviewed_windows_system_runtime(path: PurePosixPath) -> bool:
    """Reject host-specific UCRT/API-set DLLs outside the locked inventory."""

    name = path.name.casefold()
    return name == "ucrtbase.dll" or name.startswith("api-ms-win-")


def has_forbidden_suffix(path: PurePosixPath) -> bool:
    name = path.name.casefold()
    return (
        name == ".coverage"
        or name.startswith(".coverage.")
        or name.endswith(FORBIDDEN_SUFFIXES)
    )


def has_forbidden_path_part(path: PurePosixPath) -> bool:
    """Reject writable app/runtime trees while allowing pythonnet's library folder.

    ``pythonnet/runtime`` is immutable third-party application code collected by
    PyInstaller.  It is not Short Tracker's writable ``data/runtime`` state.
    Keeping this exception exact prevents a broad ``runtime`` allowance from
    weakening the release privacy boundary.
    """

    folded = tuple(part.casefold() for part in path.parts)
    if any(part in FORBIDDEN_PARTS for part in folded):
        return True
    for index, part in enumerate(folded):
        if part != "runtime":
            continue
        if index >= 2 and folded[index - 2 : index + 1] == ALLOWED_DEPENDENCY_RUNTIME_PARENT:
            continue
        return True
    return False


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def verify_third_party_licences(files: Mapping[str, bytes]) -> list[str]:
    """Validate a package-relative, hash-bound third-party licence inventory."""

    failures: list[str] = []
    if "THIRD-PARTY-NOTICES.txt" not in files:
        failures.append("package must contain THIRD-PARTY-NOTICES.txt")
    manifest_name = "LICENSES/manifest.json"
    manifest_value = files.get(manifest_name)
    if manifest_value is None:
        failures.append("package must contain LICENSES/manifest.json")
        return failures
    try:
        manifest = json.loads(manifest_value.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        failures.append(f"third-party licence manifest is invalid JSON: {exc}")
        return failures
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        failures.append("third-party licence manifest schema must be 1")
        return failures

    component_rows = manifest.get("components")
    component_ids: set[str] = set()
    component_files: dict[str, set[str]] = {}
    if not isinstance(component_rows, list):
        failures.append("third-party licence manifest components must be a list")
        component_rows = []
    for row in component_rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            failures.append("third-party licence manifest has an invalid component row")
            continue
        component_id = row["id"]
        if component_id in component_ids:
            failures.append(f"duplicate third-party component: {component_id}")
        component_ids.add(component_id)
        if not isinstance(row.get("version"), str) or not row["version"].strip():
            failures.append(f"third-party component has no version: {component_id}")
        if not isinstance(row.get("licence"), str) or not row["licence"].strip():
            failures.append(f"third-party component has no licence declaration: {component_id}")
        if not isinstance(row.get("files"), list) or not row["files"]:
            failures.append(f"third-party component has no licence files: {component_id}")
            component_files[component_id] = set()
        elif not all(isinstance(name, str) for name in row["files"]):
            failures.append(f"third-party component has invalid licence paths: {component_id}")
            component_files[component_id] = set()
        else:
            component_files[component_id] = set(row["files"])
        if not isinstance(row.get("evidence"), dict) or not row["evidence"]:
            failures.append(f"third-party component has no bundle evidence: {component_id}")
    missing_components = sorted(REQUIRED_THIRD_PARTY_COMPONENTS - component_ids)
    if missing_components:
        failures.append(
            "third-party licence manifest is missing components: "
            + ", ".join(missing_components)
        )

    inventory_rows = manifest.get("files")
    if not isinstance(inventory_rows, list):
        failures.append("third-party licence manifest files must be a list")
        inventory_rows = []
    expected_paths: set[str] = set()
    inventory_by_component: dict[str, set[str]] = {
        component_id: set() for component_id in component_ids
    }
    for row in inventory_rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            failures.append("third-party licence manifest has an invalid file row")
            continue
        name = row["path"].replace("\\", "/")
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 3
            or path.parts[0] != "LICENSES"
            or name == manifest_name
        ):
            failures.append(f"unsafe third-party licence path: {name}")
            continue
        if name in expected_paths:
            failures.append(f"duplicate third-party licence path: {name}")
            continue
        expected_paths.add(name)
        component_id = row.get("component")
        if component_id not in component_ids:
            failures.append(f"licence file references unknown component: {name}")
        else:
            inventory_by_component[component_id].add(name)
        value = files.get(name)
        if value is None:
            failures.append(f"third-party licence file is missing: {name}")
            continue
        if row.get("bytes") != len(value) or row.get("sha256") != sha256(value):
            failures.append(f"third-party licence hash/size mismatch: {name}")

    actual_paths = {
        name
        for name in files
        if name.startswith("LICENSES/") and name != manifest_name
    }
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        if missing:
            failures.append("manifested licence files are absent: " + ", ".join(missing))
        if extra:
            failures.append("unmanifested files exist under LICENSES: " + ", ".join(extra))
    for component_id in sorted(component_ids):
        if component_files.get(component_id, set()) != inventory_by_component.get(
            component_id, set()
        ):
            failures.append(
                f"third-party component file mapping does not match inventory: {component_id}"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument(
        "--forbid-string",
        action="append",
        default=[],
        help="build-machine path or other literal that must not occur in bundled bytes",
    )
    args = parser.parse_args()
    failures: list[str] = []
    with zipfile.ZipFile(args.archive) as archive:
        file_infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename.replace("\\", "/") for info in file_infos]
        folded: dict[str, str] = {}
        roots: set[str] = set()
        for name in names:
            path = PurePosixPath(name)
            roots.add(path.parts[0] if path.parts else "")
            if path.is_absolute() or ".." in path.parts or not path.parts:
                failures.append(f"unsafe archive path: {name}")
            if has_forbidden_path_part(path):
                failures.append(f"forbidden private/generated path: {name}")
            if has_forbidden_suffix(path):
                failures.append(f"forbidden private/generated suffix: {name}")
            if is_unreviewed_windows_system_runtime(path):
                failures.append(f"unreviewed Windows system runtime binary: {name}")
            previous = folded.setdefault(name.casefold(), name)
            if previous != name:
                failures.append(f"case-insensitive collision: {previous} / {name}")

        forbidden_literals = [value for value in args.forbid_string if value]
        if forbidden_literals:
            for name in names:
                value = archive.read(name)
                lowered = value.lower()
                for literal in forbidden_literals:
                    encodings = {
                        literal.casefold().encode("utf-8"),
                        literal.casefold().encode("utf-16le"),
                    }
                    if any(encoded and encoded in lowered for encoded in encodings):
                        failures.append(f"forbidden build-machine literal found in {name}")
        if len(roots) != 1:
            failures.append(f"archive must have one root directory, found: {sorted(roots)}")
        if any(PurePosixPath(name).name.casefold() == "stop short tracker.exe" for name in names):
            failures.append("archive must expose only Short Tracker.exe, not a separate Stop executable")

        if len(roots) == 1:
            root = next(iter(roots))
            prefix = root + "/"
            package_files = {
                name[len(prefix) :]: archive.read(name)
                for name in names
                if name.startswith(prefix)
            }
            failures.extend(verify_third_party_licences(package_files))

        manifest_names = [name for name in names if name.endswith("/release-manifest.json")]
        if len(manifest_names) != 1:
            failures.append("archive must contain exactly one release-manifest.json")
        else:
            manifest_name = manifest_names[0]
            manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
            prefix = manifest_name[: -len("release-manifest.json")]
            actual_by_name = {
                name[len(prefix) :]: archive.read(name)
                for name in names
                if name.startswith(prefix) and name != manifest_name
            }
            expected_by_name = {
                item["path"]: item
                for item in manifest.get("files", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
            if set(actual_by_name) != set(expected_by_name):
                failures.append("manifest file list does not match archive entries")
            for name, value in actual_by_name.items():
                expected = expected_by_name.get(name)
                if not expected:
                    continue
                if expected.get("bytes") != len(value) or expected.get("sha256") != sha256(value):
                    failures.append(f"manifest hash/size mismatch: {name}")
            if manifest.get("data_included") is not False:
                failures.append("manifest must declare data_included=false")

    if failures:
        print("Release archive verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Release archive verification passed: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
