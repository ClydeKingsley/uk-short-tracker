from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendDashboardTests(unittest.TestCase):
    def test_dashboard_calculation_contract(self):
        node = os.environ.get("SHORT_TRACKER_NODE") or shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for frontend runtime tests")
        result = subprocess.run(
            [node, "--test", str(PROJECT_ROOT / "tests" / "frontend_dashboard.test.js")],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
