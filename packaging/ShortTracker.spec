# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


repo_root = Path(SPECPATH).resolve().parent
icon_path = Path(os.environ["SHORT_TRACKER_ICON"]).resolve()
version_path = Path(os.environ["SHORT_TRACKER_VERSION_INFO"]).resolve()
manifest_path = repo_root / "packaging" / "windows.manifest"

datas = [(str(repo_root / "web"), "web")]
datas += collect_data_files("openpyxl")
datas += collect_data_files("pythonnet")
binaries = collect_dynamic_libs("pythonnet")
hiddenimports = collect_submodules("openpyxl")
hiddenimports += [
    "clr",
    "pythonnet",
    "webview.platforms.edgechromium",
    "webview.platforms.win32",
    "webview.platforms.winforms",
]

a = Analysis(
    [str(repo_root / "packaging" / "windows_entry.py")],
    pathex=[str(repo_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(repo_root / "packaging" / "pyinstaller-hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PIL",
        "_tkinter",
        "pytest",
        "tkinter",
        "webview.platforms.cef",
        "webview.platforms.cocoa",
        "webview.platforms.gtk",
        "webview.platforms.mshtml",
        "webview.platforms.qt",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Short Tracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch="x86_64",
    icon=str(icon_path),
    version=str(version_path),
    manifest=str(manifest_path),
    contents_directory="_internal",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Short Tracker",
)
