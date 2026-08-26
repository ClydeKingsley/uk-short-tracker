"""Runtime paths shared by the source and frozen Windows editions."""

from __future__ import annotations

import os
from pathlib import Path
import sys


APPLICATION_DIRECTORY = "ShortTracker"


def is_frozen() -> bool:
    """Return whether this process is running from a PyInstaller bundle."""

    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Return the read-only directory containing bundled application assets.

    PyInstaller gives imported modules a bundle-relative ``__file__``.  Keeping
    the web assets beside the bundled packages therefore makes this calculation
    work both from source and from a one-directory Windows distribution.
    """

    return Path(__file__).resolve().parents[1]


def default_data_dir(project_root: str | Path | None = None) -> Path:
    """Return the writable user-data directory for the active runtime.

    ``SHORT_TRACKER_DATA_DIR`` is an explicit advanced-user override.  Source
    checkouts stay portable and use ``PROJECT_ROOT/data``.  Frozen Windows
    builds never write beside the executable; they use the current user's
    Local AppData folder so upgrades and read-only install locations are safe.
    """

    configured = os.environ.get("SHORT_TRACKER_DATA_DIR", "").strip().strip('"')
    if configured:
        return Path(configured).expanduser().resolve()

    if is_frozen() and os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            base = Path(local_app_data)
        else:
            base = Path.home() / "AppData" / "Local"
        return (base / APPLICATION_DIRECTORY / "data").resolve()

    root = Path(project_root).resolve() if project_root is not None else resource_root()
    return root / "data"
