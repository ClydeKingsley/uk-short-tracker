from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from tools.audit_public_tree import audit, has_forbidden_file_type, main


class PublicTreeAuditTests(unittest.TestCase):
    def test_sensitive_file_types_are_fail_closed(self) -> None:
        for name in (
            "positions.tsv",
            "cache.sqlite3",
            "cache.db-shm",
            "report.pdf",
            "dataset.parquet",
            "dataset.feather",
            "events.jsonl",
            "events.ndjson",
            "model.pkl",
            "model.pickle",
            "browser.har",
            "crash.dmp",
            "symbols.pdb",
            "certificate.crt",
            "certificate.cer",
            "certificate.der",
            ".coverage.worker",
        ):
            with self.subTest(name=name):
                self.assertTrue(has_forbidden_file_type(Path(name)))

    def test_secret_patterns_and_sensitive_directories_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            label = "cloudflare_api_" + "token"
            value = "abcdefghijklmnopqrstuvwxyz012345"
            (root / "safe.txt").write_text(
                f'{label} = "{value}"\n',
                encoding="utf-8",
            )
            cache = root / "webview-profile"
            cache.mkdir()
            (cache / "state.txt").write_text("private state\n", encoding="utf-8")
            failures = "\n".join(audit(root))
        self.assertIn("Cloudflare token assignment", failures)
        self.assertIn("forbidden local-data path", failures)

    def test_normal_source_files_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("Public documentation.\n", encoding="utf-8")
            (root / "module.py").write_text("VALUE = 42\n", encoding="utf-8")
            self.assertEqual(audit(root), [])

    def test_cli_never_logs_finding_details_or_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            label = "cloudflare_api_" + "token"
            value = "abcdefghijklmnopqrstuvwxyz012345"
            (root / "safe.txt").write_text(
                f'{label} = "{value}"\n',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main([str(root)])

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("finding details were suppressed", stderr.getvalue())
        self.assertNotIn(value, stderr.getvalue())
        self.assertNotIn(str(root), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
