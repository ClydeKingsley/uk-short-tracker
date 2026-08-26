from __future__ import annotations

from pathlib import PurePosixPath
import hashlib
import json
import unittest

from tools.verify_release_archive import (
    REQUIRED_THIRD_PARTY_COMPONENTS,
    has_forbidden_path_part,
    has_forbidden_suffix,
    is_unreviewed_windows_system_runtime,
    verify_third_party_licences,
)


class ReleaseVerifierPathTests(unittest.TestCase):
    def test_allows_only_pythonnet_immutable_runtime_directory(self) -> None:
        self.assertFalse(
            has_forbidden_path_part(
                PurePosixPath(
                    "Short-Tracker-v0.2.0-windows-x64/_internal/pythonnet/runtime/Python.Runtime.dll"
                )
            )
        )

    def test_rejects_application_runtime_and_data_directories(self) -> None:
        for value in (
            "Short-Tracker/data/runtime/service.json",
            "Short-Tracker/runtime/service.json",
            "Short-Tracker/_internal/other/runtime/payload.dll",
            "Short-Tracker/_internal/pythonnet/runtime/data/private.sqlite",
        ):
            with self.subTest(value=value):
                self.assertTrue(has_forbidden_path_part(PurePosixPath(value)))

    def test_rejects_private_and_diagnostic_file_types(self) -> None:
        for value in (
            "private.tsv",
            "snapshot.sqlite3",
            "snapshot.db-wal",
            "report.pdf",
            "frame.parquet",
            "rows.feather",
            "events.jsonl",
            "events.ndjson",
            "model.pkl",
            "model.pickle",
            "session.har",
            "crash.dmp",
            "symbols.pdb",
            "identity.crt",
            "identity.cer",
            "identity.der",
            ".coverage.worker-1",
        ):
            with self.subTest(value=value):
                self.assertTrue(has_forbidden_suffix(PurePosixPath(value)))

    def test_allows_required_runtime_and_licence_types(self) -> None:
        for value in ("python311.dll", "LICENSE.txt", "release-manifest.json"):
            with self.subTest(value=value):
                self.assertFalse(has_forbidden_suffix(PurePosixPath(value)))

    def test_rejects_host_specific_ucrt_and_api_set_binaries(self) -> None:
        for value in (
            "Short-Tracker/_internal/ucrtbase.dll",
            "Short-Tracker/_internal/api-ms-win-core-file-l1-1-0.dll",
            "Short-Tracker/_internal/api-ms-win-crt-runtime-l1-1-0.dll",
        ):
            with self.subTest(value=value):
                self.assertTrue(
                    is_unreviewed_windows_system_runtime(PurePosixPath(value))
                )

        self.assertFalse(
            is_unreviewed_windows_system_runtime(
                PurePosixPath("Short-Tracker/_internal/VCRUNTIME140.dll")
            )
        )


class ThirdPartyLicenceVerifierTests(unittest.TestCase):
    def complete_inventory(self) -> dict[str, bytes]:
        files: dict[str, bytes] = {
            "THIRD-PARTY-NOTICES.txt": b"reviewed notices\n",
        }
        components = []
        inventory = []
        for component in sorted(REQUIRED_THIRD_PARTY_COMPONENTS):
            path = f"LICENSES/{component}/LICENSE.txt"
            value = f"complete terms for {component}\n".encode()
            files[path] = value
            components.append(
                {
                    "id": component,
                    "name": component,
                    "version": "1.0.0",
                    "licence": "reviewed",
                    "evidence": {"kind": "test"},
                    "files": [path],
                }
            )
            inventory.append(
                {
                    "path": path,
                    "component": component,
                    "bytes": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(),
                }
            )
        files["LICENSES/manifest.json"] = json.dumps(
            {"schema": 1, "components": components, "files": inventory}
        ).encode()
        return files

    def test_complete_hash_bound_inventory_passes(self) -> None:
        self.assertEqual(verify_third_party_licences(self.complete_inventory()), [])

    def test_missing_component_tamper_and_unmanifested_file_are_rejected(self) -> None:
        files = self.complete_inventory()
        manifest = json.loads(files["LICENSES/manifest.json"])
        removed = manifest["components"].pop()
        files["LICENSES/manifest.json"] = json.dumps(manifest).encode()
        tampered_path = manifest["files"][0]["path"]
        files[tampered_path] += b"tampered"
        files["LICENSES/unreviewed/LICENSE.txt"] = b"unreviewed"
        failures = "\n".join(verify_third_party_licences(files))
        self.assertIn(removed["id"], failures)
        self.assertIn("hash/size mismatch", failures)
        self.assertIn("unmanifested files", failures)


if __name__ == "__main__":
    unittest.main()
