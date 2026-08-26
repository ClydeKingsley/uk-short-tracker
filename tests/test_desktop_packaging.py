from __future__ import annotations

from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DesktopPackagingContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (PROJECT_ROOT / relative).read_text(encoding="utf-8")

    def test_release_exposes_no_separate_stop_executable(self) -> None:
        build_script = self.read("packaging/Build-WindowsRelease.ps1")
        package_readme = self.read("packaging/README-Windows.txt")
        no_python_test = self.read("packaging/Test-NoPythonRuntime.ps1")
        self.assertNotIn("Stop Short Tracker.exe", build_script)
        self.assertNotIn("Stop Short Tracker.exe", package_readme)
        self.assertNotIn("Stop Short Tracker.exe", no_python_test)

    def test_frozen_entry_uses_desktop_window_not_executable_name_dispatch(self) -> None:
        entry = self.read("packaging/windows_entry.py")
        self.assertIn("from launcher.desktop_launcher import run_desktop", entry)
        self.assertIn("return run_desktop()", entry)
        self.assertNotIn("executable_name.startswith", entry)
        self.assertNotIn("run_gui", entry)

    def test_spec_bundles_edgechromium_and_excludes_legacy_gui_engines(self) -> None:
        spec = self.read("packaging/ShortTracker.spec")
        hook = self.read("packaging/pyinstaller-hooks/hook-webview.py")
        self.assertIn('"webview.platforms.edgechromium"', spec)
        self.assertIn('"webview.platforms.winforms"', spec)
        self.assertIn('"webview.platforms.mshtml"', spec)
        self.assertIn('"tkinter"', spec)
        self.assertIn('"pyinstaller-hooks"', spec)
        self.assertIn('"win-x64"', hook)
        self.assertIn('"WebView2Loader.dll"', hook)
        self.assertNotIn('"WebBrowserInterop.x64.dll"', hook)

    def test_bundle_self_test_requires_edgechromium_renderer(self) -> None:
        entry = self.read("packaging/windows_entry.py")
        build_script = self.read("packaging/Build-WindowsRelease.ps1")
        no_python_test = self.read("packaging/Test-NoPythonRuntime.ps1")
        self.assertIn("_configure_pythonnet_runtime()", entry)
        self.assertIn('initialize("edgechromium")', entry)
        self.assertIn('renderer == "edgechromium"', entry)
        self.assertIn("$bundleSelfTest.webview_renderer -ne 'edgechromium'", build_script)
        self.assertIn("-Stream Zone.Identifier", build_script)
        self.assertIn("-Stream Zone.Identifier", no_python_test)

    def test_pythonnet_config_allows_downloaded_managed_assemblies(self) -> None:
        config_path = PROJECT_ROOT / "launcher" / "pythonnet-netfx.config"
        root = ET.parse(config_path).getroot()
        remote_sources = root.find("./runtime/loadFromRemoteSources")
        self.assertIsNotNone(remote_sources)
        self.assertEqual(remote_sources.attrib.get("enabled"), "true")
        launcher = self.read("launcher/desktop_launcher.py")
        spec = self.read("packaging/ShortTracker.spec")
        self.assertIn('domain=PYTHONNET_APPDOMAIN', launcher)
        self.assertIn('config_file=PYTHONNET_CONFIG_PATH', launcher)
        self.assertIn('"pythonnet-netfx.config"', spec)
        build_script = self.read("packaging/Build-WindowsRelease.ps1")
        self.assertIn("'Short Tracker.exe.config'", build_script)

    def test_release_checksum_verification_normalizes_windows_crlf(self) -> None:
        workflow = self.read(".github/workflows/release.yml")
        self.assertEqual(workflow.count("| tr -d '\\r'"), 2)
        self.assertIn('expected_hash="$(awk', workflow)
        self.assertIn('expected_name="$(awk', workflow)


if __name__ == "__main__":
    unittest.main()
