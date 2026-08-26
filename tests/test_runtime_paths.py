from __future__ import annotations

import os
from pathlib import Path
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from short_tracker.__main__ import _build_parser
from short_tracker.paths import default_data_dir


class RuntimePathTests(unittest.TestCase):
    def test_cli_help_renders_windows_data_directory_literal(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            _build_parser().parse_args(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("%LOCALAPPDATA%/ShortTracker/data", output.getvalue())

    def test_source_default_stays_inside_the_selected_project(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("short_tracker.paths.is_frozen", return_value=False),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("SHORT_TRACKER_DATA_DIR", None)
            root = Path(temporary).resolve()
            self.assertEqual(default_data_dir(root), root / "data")

    def test_explicit_data_directory_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configured = Path(temporary) / "chosen"
            with patch.dict(
                os.environ,
                {"SHORT_TRACKER_DATA_DIR": str(configured)},
                clear=False,
            ):
                self.assertEqual(default_data_dir(Path("unused")), configured.resolve())

    @unittest.skipUnless(os.name == "nt", "Windows Local AppData contract")
    def test_frozen_windows_default_uses_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local_app_data = Path(temporary).resolve()
            with (
                patch("short_tracker.paths.is_frozen", return_value=True),
                patch.dict(
                    os.environ,
                    {
                        "LOCALAPPDATA": str(local_app_data),
                        "SHORT_TRACKER_DATA_DIR": "",
                    },
                    clear=False,
                ),
            ):
                self.assertEqual(
                    default_data_dir(Path("unused")),
                    local_app_data / "ShortTracker" / "data",
                )


if __name__ == "__main__":
    unittest.main()
