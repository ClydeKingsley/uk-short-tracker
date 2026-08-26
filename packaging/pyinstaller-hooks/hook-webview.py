"""Collect only the Windows x64 Edge Chromium assets used by Short Tracker."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, get_package_paths


_, package_dir = get_package_paths("webview")
webview_root = Path(package_dir)
lib_root = webview_root / "lib"

datas = collect_data_files("webview", subdir="js")
datas += [
    (str(lib_root / "Microsoft.Web.WebView2.Core.dll"), "webview/lib"),
    (str(lib_root / "Microsoft.Web.WebView2.WinForms.dll"), "webview/lib"),
]
# pywebview 6.2.1 resolves all three runtime directories while importing its
# Edge Chromium backend, even though this release executes only the x64 loader.
# Retain the three small official loaders so that import is deterministic, but
# omit the legacy WebBrowserInterop DLLs and Android/CEF assets.
binaries = [
    (
        str(lib_root / "runtimes" / platform / "native" / "WebView2Loader.dll"),
        f"webview/lib/runtimes/{platform}/native",
    )
    for platform in ("win-arm64", "win-x64", "win-x86")
]
