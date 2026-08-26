from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from tools.verify_publication_config import audit_publication_config


VALID_LICENSE = """MIT License

Copyright (c) 2026 Project Maintainer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class PublicationConfigurationTests(unittest.TestCase):
    def make_tree(
        self,
        root: Path,
        *,
        owner: str = "ukshort",
        repository: str = "short-tracker",
    ) -> None:
        (root / "short_tracker").mkdir()
        (root / "LICENSE").write_text(VALID_LICENSE, encoding="utf-8")
        (root / "pyproject.toml").write_text(
            textwrap.dedent(
                """
                [project]
                name = "uk-short-tracker"
                license = "MIT"
                license-files = ["LICENSE"]
                """
            ).lstrip(),
            encoding="utf-8",
        )
        (root / "short_tracker" / "update.py").write_text(
            textwrap.dedent(
                f'''\
                from typing import Final
                DEFAULT_GITHUB_OWNER: Final[str] = "{owner}"
                DEFAULT_GITHUB_REPOSITORY: Final[str] = "{repository}"
                '''
            ),
            encoding="utf-8",
        )

    def test_complete_configuration_passes_and_matches_actions_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_tree(root)
            failures = audit_publication_config(
                root,
                expected_repository="ukshort/short-tracker",
            )
        self.assertEqual(failures, [])

    def test_missing_licence_metadata_and_repository_are_all_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "short_tracker").mkdir()
            (root / "LICENSE-DECISION-REQUIRED.md").write_text(
                "decision pending",
                encoding="utf-8",
            )
            (root / "pyproject.toml").write_text(
                '[project]\nname = "uk-short-tracker"\n',
                encoding="utf-8",
            )
            (root / "short_tracker" / "update.py").write_text(
                'DEFAULT_GITHUB_OWNER = ""\nDEFAULT_GITHUB_REPOSITORY = ""\n',
                encoding="utf-8",
            )
            failures = audit_publication_config(root)
        joined = "\n".join(failures)
        self.assertIn("LICENSE is missing", joined)
        self.assertIn("LICENSE-DECISION-REQUIRED.md must be removed", joined)
        self.assertIn("[project].license", joined)
        self.assertIn("[project].license-files", joined)
        self.assertIn("DEFAULT_GITHUB_OWNER", joined)
        self.assertIn("DEFAULT_GITHUB_REPOSITORY", joined)

    def test_repository_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_tree(root)
            failures = audit_publication_config(
                root,
                expected_repository="another-owner/short-tracker",
            )
        self.assertTrue(any("does not match" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
