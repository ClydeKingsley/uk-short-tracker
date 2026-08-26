"""Collect and attest complete third-party terms for the Windows bundle."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from typing import Any


@dataclass(frozen=True, slots=True)
class DistributionComponent:
    component_id: str
    display_name: str
    distribution: str
    version: str
    licence: str
    module: str
    requires_distribution_terms: bool = True


DISTRIBUTION_COMPONENTS = (
    DistributionComponent("openpyxl", "openpyxl", "openpyxl", "3.1.5", "MIT", "openpyxl"),
    DistributionComponent(
        "et-xmlfile", "et_xmlfile", "et-xmlfile", "2.0.0", "MIT", "et_xmlfile"
    ),
    DistributionComponent(
        "pywebview", "pywebview", "pywebview", "6.2.1", "BSD-3-Clause", "webview"
    ),
    DistributionComponent(
        "pythonnet", "pythonnet", "pythonnet", "3.1.0", "MIT", "pythonnet"
    ),
    DistributionComponent(
        "clr-loader", "clr-loader", "clr-loader", "0.3.1", "MIT", "clr_loader"
    ),
    DistributionComponent("cffi", "cffi", "cffi", "2.1.1", "MIT-0", "cffi"),
    DistributionComponent(
        "pycparser", "pycparser", "pycparser", "3.0", "BSD-3-Clause", "pycparser"
    ),
    DistributionComponent("bottle", "Bottle", "bottle", "0.13.4", "MIT", "bottle"),
    DistributionComponent(
        "proxy-tools",
        "proxy-tools",
        "proxy-tools",
        "0.1.0",
        "Upstream BSD-form text; PyPI/sdist package metadata declares MIT",
        "proxy_tools",
        requires_distribution_terms=False,
    ),
    DistributionComponent(
        "typing-extensions",
        "typing_extensions",
        "typing-extensions",
        "4.16.0",
        "PSF-2.0",
        "typing_extensions",
    ),
    DistributionComponent(
        "setuptools", "setuptools and bundled vendors", "setuptools", "84.0.0", "MIT", "setuptools"
    ),
    DistributionComponent(
        "packaging",
        "packaging",
        "packaging",
        "26.3",
        "Apache-2.0 OR BSD-2-Clause",
        "packaging",
    ),
)

SUPPLEMENTAL_HASHES = {
    "microsoft-webview2-1.0.3856.49/LICENSE.txt": "9995174528dba139ca753d02d8667dbec49f65ab17e65c643a914f2b58cfb4a2",
    "microsoft-webview2-1.0.3856.49/NOTICE.txt": "b75b4332450ff7b8dab78770247022b89f58cdde358a5d2afbd9e246d6e01caf",
    "netstandard-library-2.0.3/ASSEMBLIES.txt": "9527b30dfd5ecba6acdaf5ae3495e987b1244d5e1e6690e3523f9cd005070475",
    "netstandard-library-2.0.3/LICENSE.txt": "18647d26bc4c4d892a5fd29de1264d10906b3124cafa98313300ff300441c7f0",
    "netstandard-library-2.0.3/THIRD-PARTY-NOTICES.txt": "dc105f6e48b77cf7d89aa964bb4be2d50b24ff183d7643371ded3f499a42a49c",
    "proxy-tools-0.1.0/LICENSE.txt": "f96cf2b17c9b0cede77438165fdc6ea2f91bedb248d1779538727a2b93d71e12",
    "openssl-3.5.7/LICENSE.txt": "ff420781e0005270cd1894a265e526dec4eb332f99df62a481b06672db202290",
    "libffi-3.4.4/LICENSE.txt": "62f7c24c5fb5935249293019030d9abf3b132d07c75242c1ee91035a12bd04df",
    "sqlite-3.53.1/LICENSE.md": "5fd96120d2597155cbbdff87c8b9554d3915f0fe85834a75032d1e4a08b6c2a4",
}

_LICENCE_NAME = re.compile(
    r"^(?:licen[cs]e|copying|notice|authors|copyright)(?:[._-].*)?$",
    re.IGNORECASE,
)
_WEBVIEW2_VERSION = "1.0.3856.49"
_NETSTANDARD_VERSION = "2.0.3"
_OPENSSL_VERSION = "3.5.7"
_LIBFFI_VERSION = "3.4.4"
_SQLITE_VERSION = "3.53.1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {value}")
    return path


def _is_licence_record(path: PurePosixPath) -> bool:
    return bool(_LICENCE_NAME.fullmatch(path.name))


def _pyz_modules(path: Path) -> set[str]:
    payload = ast.literal_eval(path.read_text(encoding="utf-8"))
    if not isinstance(payload, tuple) or len(payload) != 2 or not isinstance(payload[1], list):
        raise ValueError("unexpected PyInstaller PYZ table-of-contents format")
    modules: set[str] = set()
    for item in payload[1]:
        if isinstance(item, tuple) and item and isinstance(item[0], str):
            modules.add(item[0])
    return modules


def _has_module(modules: set[str], prefix: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for name in modules)


def _pe_file_version(path: Path) -> str:
    try:
        import pefile
    except ImportError as exc:  # pragma: no cover - release environment contract
        raise RuntimeError("pefile is required to verify WebView2 binaries") from exc
    pe = pefile.PE(str(path), fast_load=False)
    try:
        for group in pe.FileInfo or []:
            for block in group:
                for table in getattr(block, "StringTable", ()) or ():
                    raw = table.entries.get(b"FileVersion")
                    if raw:
                        return raw.decode("utf-8", errors="strict").strip()
    finally:
        pe.close()
    raise ValueError(f"PE file has no FileVersion: {path.name}")


class Collector:
    def __init__(self, repo: Path, release_root: Path, pyz_toc: Path, output: Path) -> None:
        self.repo = repo.resolve()
        self.release_root = release_root.resolve()
        self.pyz_toc = pyz_toc.resolve()
        self.output = output.resolve()
        self.source_root = self.repo / "packaging" / "license-sources"
        self.runtime_lock_path = self.repo / "packaging" / "windows-runtime-lock.json"
        self.modules = _pyz_modules(self.pyz_toc)
        self.components: list[dict[str, Any]] = []
        self.files: list[dict[str, Any]] = []
        self.runtime_lock: dict[str, Any] = {}
        self.runtime_files: dict[str, dict[str, Any]] = {}

    def validate_paths(self) -> None:
        if not self.release_root.is_dir():
            raise ValueError(f"release root does not exist: {self.release_root}")
        unexpected_system_runtime = sorted(
            (
                path.relative_to(self.release_root).as_posix()
                for path in self.release_root.rglob("*.dll")
                if path.name.casefold() == "ucrtbase.dll"
                or path.name.casefold().startswith("api-ms-win-")
            ),
            key=str.casefold,
        )
        if unexpected_system_runtime:
            raise ValueError(
                "system-provided Windows UCRT/API-set DLLs must not be bundled: "
                + ", ".join(unexpected_system_runtime)
            )
        if not self.pyz_toc.is_file():
            raise ValueError(f"PyInstaller PYZ table of contents is missing: {self.pyz_toc}")
        if self.output.parent != self.release_root or self.output.name != "LICENSES":
            raise ValueError("output must be the release root's direct LICENSES directory")
        if self.output.exists():
            raise ValueError(f"refusing to overwrite existing licence output: {self.output}")
        try:
            lock = json.loads(self.runtime_lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Windows runtime lock: {exc}") from exc
        if not isinstance(lock, dict) or lock.get("schema") != 1:
            raise ValueError("Windows runtime lock schema must be 1")
        python_row = lock.get("python")
        rows = lock.get("files")
        if not isinstance(python_row, dict) or not isinstance(rows, list):
            raise ValueError("Windows runtime lock is missing Python or file inventory")
        runtime_files: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError("Windows runtime lock has an invalid file row")
            relative = _safe_relative(row["path"]).as_posix()
            if relative in runtime_files:
                raise ValueError(f"duplicate Windows runtime lock path: {relative}")
            if not isinstance(row.get("bytes"), int) or not re.fullmatch(
                r"[0-9a-f]{64}", str(row.get("sha256", ""))
            ):
                raise ValueError(f"invalid Windows runtime fingerprint: {relative}")
            runtime_files[relative] = row
        self.runtime_lock = lock
        self.runtime_files = runtime_files

    def verify_runtime_files(self, relative_paths: tuple[str, ...]) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for relative in relative_paths:
            expected = self.runtime_files.get(relative)
            if expected is None:
                raise ValueError(f"Windows runtime lock does not cover: {relative}")
            path = self.release_root.joinpath(*PurePosixPath(relative).parts)
            if not path.is_file():
                raise ValueError(f"locked Windows runtime file is missing: {relative}")
            value = path.read_bytes()
            actual_hash = sha256_bytes(value)
            if len(value) != expected["bytes"] or actual_hash != expected["sha256"]:
                raise ValueError(f"locked Windows runtime fingerprint mismatch: {relative}")
            expected_version = expected.get("file_version")
            if expected_version is not None:
                actual_version = _pe_file_version(path)
                if actual_version != expected_version:
                    raise ValueError(
                        f"locked Windows runtime version mismatch: {relative} "
                        f"({actual_version} != {expected_version})"
                    )
            evidence.append(
                {
                    "path": relative,
                    "bytes": len(value),
                    "sha256": actual_hash,
                    "file_version": expected_version,
                }
            )
        return evidence

    def copy_file(self, source: Path, relative: PurePosixPath, component_id: str) -> str:
        if not source.is_file():
            raise ValueError(f"required licence source is missing: {source.name}")
        destination = self.release_root.joinpath(*relative.parts)
        try:
            destination.resolve(strict=False).relative_to(self.release_root)
        except ValueError as exc:
            raise ValueError(f"licence destination leaves release root: {relative}") from exc
        if destination.exists():
            raise ValueError(f"duplicate licence destination: {relative.as_posix()}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        value = destination.read_bytes()
        path_text = relative.as_posix()
        self.files.append(
            {
                "path": path_text,
                "component": component_id,
                "bytes": len(value),
                "sha256": sha256_bytes(value),
            }
        )
        return path_text

    def copy_supplemental(self, source_relative: str, component_id: str) -> str:
        expected = SUPPLEMENTAL_HASHES[source_relative]
        source = self.source_root.joinpath(*PurePosixPath(source_relative).parts)
        actual = sha256_bytes(source.read_bytes()) if source.is_file() else "missing"
        if actual != expected:
            raise ValueError(
                f"supplemental licence source hash mismatch: {source_relative} ({actual})"
            )
        destination = PurePosixPath("LICENSES", component_id, Path(source_relative).name)
        return self.copy_file(source, destination, component_id)

    def collect_distribution(self, component: DistributionComponent) -> None:
        if not _has_module(self.modules, component.module):
            raise ValueError(
                f"expected bundled module is absent from PYZ: {component.module}"
            )
        distribution = metadata.distribution(component.distribution)
        if distribution.version != component.version:
            raise ValueError(
                f"{component.distribution} {component.version} is required; "
                f"found {distribution.version}"
            )
        copied: list[str] = []
        for record in sorted(distribution.files or (), key=lambda item: str(item).casefold()):
            raw_record = PurePosixPath(str(record).replace("\\", "/"))
            if not _is_licence_record(raw_record):
                continue
            relative_record = _safe_relative(str(record))
            source = Path(distribution.locate_file(record)).resolve()
            destination = PurePosixPath(
                "LICENSES",
                f"{component.component_id}-{component.version}",
                "distribution",
                *relative_record.parts,
            )
            copied.append(self.copy_file(source, destination, component.component_id))
        if component.requires_distribution_terms and not copied:
            raise ValueError(
                f"installed {component.distribution} distribution has no complete licence files"
            )
        if component.component_id == "proxy-tools":
            copied.append(
                self.copy_supplemental("proxy-tools-0.1.0/LICENSE.txt", component.component_id)
            )
        self.components.append(
            {
                "id": component.component_id,
                "name": component.display_name,
                "version": component.version,
                "licence": component.licence,
                "evidence": {"kind": "pyinstaller-pyz-module", "module": component.module},
                "files": sorted(copied, key=str.casefold),
            }
        )

    def collect_python(self) -> None:
        python_row = self.runtime_lock["python"]
        expected_version = python_row.get("version")
        if sys.version.split()[0] != expected_version:
            raise ValueError(
                f"locked Python {expected_version} is required; found {sys.version.split()[0]}"
            )
        build_path = Path(sys.base_prefix) / "BUILD"
        build = build_path.read_text(encoding="utf-8").strip() if build_path.is_file() else ""
        if build != python_row.get("build"):
            raise ValueError(f"locked Python build {python_row.get('build')} is required; found {build}")
        runtime_evidence = self.verify_runtime_files(tuple(sorted(self.runtime_files)))
        source = Path(sys.base_prefix) / "LICENSE.txt"
        source_value = source.read_bytes()
        if (
            len(source_value) != python_row.get("license_bytes")
            or sha256_bytes(source_value) != python_row.get("license_sha256")
        ):
            raise ValueError("locked Python LICENSE.txt fingerprint mismatch")
        component_id = "python"
        copied = self.copy_file(
            source,
            PurePosixPath("LICENSES", f"python-{expected_version}", "LICENSE.txt"),
            component_id,
        )
        self.components.append(
            {
                "id": component_id,
                "name": "Python",
                "version": expected_version,
                "licence": "PSF-2.0 and bundled third-party notices",
                "evidence": {
                    "kind": "locked-uv-managed-cpython-runtime",
                    "distribution": python_row.get("distribution"),
                    "build": build,
                    "source": python_row.get("source"),
                    "files": runtime_evidence,
                },
                "files": [copied],
            }
        )

    def collect_openssl(self) -> None:
        paths = ("_internal/libcrypto-3-x64.dll", "_internal/libssl-3-x64.dll")
        evidence = self.verify_runtime_files(paths)
        copied = self.copy_supplemental(
            f"openssl-{_OPENSSL_VERSION}/LICENSE.txt", "openssl"
        )
        self.components.append(
            {
                "id": "openssl",
                "name": "OpenSSL runtime libraries bundled by CPython",
                "version": _OPENSSL_VERSION,
                "licence": "Apache-2.0",
                "evidence": {"kind": "locked-bundle-paths", "files": evidence},
                "files": [copied],
            }
        )

    def collect_libffi(self) -> None:
        evidence = self.verify_runtime_files(("_internal/libffi-8.dll",))
        copied = self.copy_supplemental(
            f"libffi-{_LIBFFI_VERSION}/LICENSE.txt", "libffi"
        )
        self.components.append(
            {
                "id": "libffi",
                "name": "libffi ABI 8 runtime library bundled by CPython",
                "version": _LIBFFI_VERSION,
                "licence": "MIT",
                "evidence": {"kind": "locked-bundle-path", "files": evidence},
                "files": [copied],
            }
        )

    def collect_sqlite(self) -> None:
        evidence = self.verify_runtime_files(("_internal/sqlite3.dll",))
        copied = self.copy_supplemental(
            f"sqlite-{_SQLITE_VERSION}/LICENSE.md", "sqlite"
        )
        self.components.append(
            {
                "id": "sqlite",
                "name": "SQLite runtime library bundled by CPython",
                "version": _SQLITE_VERSION,
                "licence": "Public domain dedication (SQLite Blessing)",
                "evidence": {"kind": "locked-bundle-path", "files": evidence},
                "files": [copied],
            }
        )

    def collect_pyinstaller_bootloader(self) -> None:
        executable = self.release_root / "Short Tracker.exe"
        if not executable.is_file():
            raise ValueError("Short Tracker.exe is missing")
        distribution = metadata.distribution("pyinstaller")
        expected = "6.22.2"
        if distribution.version != expected:
            raise ValueError(f"PyInstaller {expected} is required; found {distribution.version}")
        records = [
            record
            for record in distribution.files or ()
            if _is_licence_record(PurePosixPath(str(record).replace("\\", "/")))
        ]
        if not records:
            raise ValueError("PyInstaller's bootloader terms are missing")
        copied: list[str] = []
        for record in sorted(records, key=lambda item: str(item).casefold()):
            relative_record = _safe_relative(str(record))
            copied.append(
                self.copy_file(
                    Path(distribution.locate_file(record)).resolve(),
                    PurePosixPath(
                        "LICENSES",
                        f"pyinstaller-bootloader-{expected}",
                        *relative_record.parts,
                    ),
                    "pyinstaller-bootloader",
                )
            )
        self.components.append(
            {
                "id": "pyinstaller-bootloader",
                "name": "PyInstaller bootloader and related files",
                "version": expected,
                "licence": "GPL-2.0-or-later WITH Bootloader-exception",
                "evidence": {"kind": "bundle-path", "path": "Short Tracker.exe"},
                "files": sorted(copied, key=str.casefold),
            }
        )

    def collect_webview2(self) -> None:
        relative_paths = (
            "_internal/webview/lib/Microsoft.Web.WebView2.Core.dll",
            "_internal/webview/lib/Microsoft.Web.WebView2.WinForms.dll",
            "_internal/webview/lib/runtimes/win-arm64/native/WebView2Loader.dll",
            "_internal/webview/lib/runtimes/win-x64/native/WebView2Loader.dll",
            "_internal/webview/lib/runtimes/win-x86/native/WebView2Loader.dll",
        )
        for relative in relative_paths:
            binary = self.release_root.joinpath(*PurePosixPath(relative).parts)
            if not binary.is_file():
                raise ValueError(f"expected WebView2 binary is missing: {relative}")
            version = _pe_file_version(binary)
            if version != _WEBVIEW2_VERSION:
                raise ValueError(
                    f"WebView2 terms are for {_WEBVIEW2_VERSION}, but {relative} is {version}"
                )
        component_id = "microsoft-webview2"
        copied = [
            self.copy_supplemental(
                f"microsoft-webview2-{_WEBVIEW2_VERSION}/{name}", component_id
            )
            for name in ("LICENSE.txt", "NOTICE.txt")
        ]
        self.components.append(
            {
                "id": component_id,
                "name": "Microsoft Edge WebView2 SDK loader and .NET interop assemblies",
                "version": _WEBVIEW2_VERSION,
                "licence": "Microsoft WebView2 SDK redistribution terms and notices",
                "evidence": {"kind": "bundle-paths", "paths": list(relative_paths)},
                "files": sorted(copied, key=str.casefold),
            }
        )

    def collect_netstandard(self) -> None:
        runtime = self.release_root / "_internal" / "pythonnet" / "runtime"
        if not runtime.is_dir():
            raise ValueError("pythonnet runtime directory is missing")
        actual_paths = sorted(
            (
                path.relative_to(self.release_root).as_posix()
                for path in runtime.glob("*.dll")
                if path.name != "Python.Runtime.dll"
            ),
            key=str.casefold,
        )
        if len(actual_paths) != 96:
            raise ValueError(
                f"expected 96 bundled .NET Standard reference assemblies; found {len(actual_paths)}"
            )
        assembly_source = self.source_root / f"netstandard-library-{_NETSTANDARD_VERSION}" / "ASSEMBLIES.txt"
        reviewed_names = {
            line.strip()
            for line in assembly_source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        unknown = sorted(
            {PurePosixPath(path).name for path in actual_paths} - reviewed_names,
            key=str.casefold,
        )
        if unknown:
            raise ValueError(f"unreviewed .NET reference assemblies: {', '.join(unknown)}")
        component_id = "dotnet-standard"
        copied = [
            self.copy_supplemental(
                f"netstandard-library-{_NETSTANDARD_VERSION}/{name}", component_id
            )
            for name in ("ASSEMBLIES.txt", "LICENSE.txt", "THIRD-PARTY-NOTICES.txt")
        ]
        self.components.append(
            {
                "id": component_id,
                "name": ".NET Standard reference assemblies bundled by pythonnet",
                "version": _NETSTANDARD_VERSION,
                "licence": "MIT and bundled third-party notices",
                "evidence": {"kind": "bundle-paths", "paths": actual_paths},
                "files": sorted(copied, key=str.casefold),
            }
        )

    def write_manifest(self) -> None:
        payload = {
            "schema": 1,
            "purpose": "Complete third-party terms for the verified Windows bundle",
            "components": sorted(self.components, key=lambda item: item["id"]),
            "files": sorted(self.files, key=lambda item: item["path"].casefold()),
        }
        manifest = self.output / "manifest.json"
        manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def run(self) -> None:
        self.validate_paths()
        self.output.mkdir()
        self.collect_python()
        self.collect_openssl()
        self.collect_libffi()
        self.collect_sqlite()
        for component in DISTRIBUTION_COMPONENTS:
            self.collect_distribution(component)
        self.collect_netstandard()
        self.collect_webview2()
        self.collect_pyinstaller_bootloader()
        self.write_manifest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--pyz-toc", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    release_root = args.release_root.resolve()
    output = args.output or release_root / "LICENSES"
    try:
        Collector(args.repo, release_root, args.pyz_toc, output).run()
    except (OSError, ValueError, RuntimeError, metadata.PackageNotFoundError) as exc:
        print(f"Third-party licence collection failed: {exc}", file=sys.stderr)
        return 1
    print(f"Third-party licence collection passed: {Path(output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
