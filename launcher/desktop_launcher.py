"""One-click background launcher for the local Short Tracker service.

The module deliberately uses only the Python standard library so the launcher
can explain a missing application dependency instead of failing silently.  It
never binds a public interface and never terminates an unknown process.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid
import webbrowser

from short_tracker import __version__
from short_tracker.paths import default_data_dir, is_frozen, resource_root


SERVICE_NAME = "UK Short Tracker"
SERVICE_MODE = "local_read_only_research"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8777
SERVICE_PROTOCOL = 1
STATE_SCHEMA = 3
PROJECT_ROOT = resource_root()
START_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 12.0
DESKTOP_CLOSE_SYNC_TIMEOUT_SECONDS = 15 * 60.0
DESKTOP_CLOSE_POLL_SECONDS = 0.5
WEBVIEW2_MINIMUM_VERSION = (86, 0, 622, 0)
WEBVIEW2_RUNTIME_PRODUCT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
DOTNET_FRAMEWORK_462_RELEASE = 394802
WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
PYTHONNET_APPDOMAIN = "ShortTrackerDesktop"
PYTHONNET_CONFIG_PATH = PROJECT_ROOT / "launcher" / "pythonnet-netfx.config"


class LauncherError(RuntimeError):
    """A launcher failure whose message is safe to show to the user."""


class LauncherBusyError(LauncherError):
    """Another launcher action currently owns the runtime lock."""


class DesktopAlreadyRunningError(LauncherError):
    """Another visible Short Tracker desktop window owns the UI lock."""


@dataclass(frozen=True, slots=True)
class PythonCommand:
    prefix: tuple[str, ...]
    display: str
    bundled: bool = False


@dataclass(slots=True)
class ActionResult:
    ok: bool
    message: str
    url: str | None = None
    log_path: Path | None = None
    already_running: bool = False
    attention: bool = False
    browser_opened: bool | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
            str(Path(right).resolve())
        )
    except (OSError, TypeError, ValueError):
        return False


def _query_process_identity(pid: int) -> tuple[str, dict[str, Any] | None]:
    """Return (alive/dead/unknown, stable identity) without changing a process."""

    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "dead", None

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            synchronize = 0x00100000
            wait_object_0 = 0x00000000
            wait_timeout = 0x00000102

            class FileTime(ctypes.Structure):
                _fields_ = [
                    ("low", wintypes.DWORD),
                    ("high", wintypes.DWORD),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime),
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL

            handle = kernel32.OpenProcess(
                process_query_limited_information | synchronize,
                False,
                pid,
            )
            if not handle:
                error = ctypes.get_last_error()
                return ("dead", None) if error in {87, 1168} else ("unknown", None)
            try:
                wait_result = kernel32.WaitForSingleObject(handle, 0)
                if wait_result == wait_object_0:
                    return "dead", None
                if wait_result != wait_timeout:
                    return "unknown", None

                capacity = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(capacity.value)
                if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
                    return "unknown", None

                creation = FileTime()
                exit_time = FileTime()
                kernel_time = FileTime()
                user_time = FileTime()
                if not kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                ):
                    return "unknown", None
                creation_id = (int(creation.high) << 32) | int(creation.low)
                return "alive", {
                    "pid": pid,
                    "executable": str(Path(buffer.value).resolve()),
                    "creation_id": str(creation_id),
                }
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return "unknown", None

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead", None
    except PermissionError:
        return "unknown", None
    except OSError:
        return "dead", None

    proc_root = Path("/proc") / str(pid)
    try:
        executable = str((proc_root / "exe").resolve())
        stat_fields = (proc_root / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        creation_id = stat_fields[19]
    except (OSError, IndexError):
        return "unknown", None
    return "alive", {
        "pid": pid,
        "executable": executable,
        "creation_id": str(creation_id),
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@contextmanager
def _runtime_lock(path: Path, *, timeout: float = 25.0) -> Iterator[None]:
    """Hold a one-byte OS lock while start/stop state is being changed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()

        deadline = time.monotonic() + timeout
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LauncherBusyError(
                            "另一个启动或停止操作仍在进行，请稍后重试。\n"
                            "Another launcher action is still in progress."
                        )
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LauncherBusyError("Another launcher action is still in progress.")
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def _desktop_window_lock(path: Path) -> Iterator[None]:
    """Hold a non-blocking OS lock for the lifetime of the visible window."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()

        if os.name == "nt":
            import msvcrt

            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise DesktopAlreadyRunningError(
                    "Short Tracker 桌面窗口已经打开。\n"
                    "Short Tracker is already open."
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DesktopAlreadyRunningError(
                    "Short Tracker desktop window is already open."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _numeric_version(value: object) -> tuple[int, ...] | None:
    text = str(value or "").strip()
    parts = text.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _dotnet_framework_release() -> int | None:
    """Return the installed .NET Framework v4 Full release marker on Windows."""

    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None

    values: list[int] = []
    views = [0]
    for name in ("KEY_WOW64_32KEY", "KEY_WOW64_64KEY"):
        value = int(getattr(winreg, name, 0))
        if value and value not in views:
            views.append(value)
    for view in views:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
                0,
                winreg.KEY_READ | view,
            ) as key:
                release, _ = winreg.QueryValueEx(key, "Release")
                values.append(int(release))
        except (OSError, TypeError, ValueError):
            continue
    return max(values) if values else None


def _webview2_runtime_version() -> str | None:
    """Return the newest registered Evergreen WebView2 Runtime version."""

    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None

    paths = (
        rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_PRODUCT_ID}",
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_PRODUCT_ID}",
    )
    views = [0]
    for name in ("KEY_WOW64_32KEY", "KEY_WOW64_64KEY"):
        value = int(getattr(winreg, name, 0))
        if value and value not in views:
            views.append(value)

    candidates: list[tuple[tuple[int, ...], str]] = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for path in paths:
            for view in views:
                try:
                    with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as key:
                        version, _ = winreg.QueryValueEx(key, "pv")
                except OSError:
                    continue
                numeric = _numeric_version(version)
                if numeric is not None:
                    candidates.append((numeric, str(version).strip()))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _require_windows_webview2() -> str:
    """Validate the renderer prerequisites before starting the local service."""

    if os.name != "nt":
        raise LauncherError(
            "此桌面发布版仅支持 Windows 10/11。\n"
            "This desktop build supports Windows 10/11 only."
        )
    dotnet_release = _dotnet_framework_release()
    if dotnet_release is None or dotnet_release < DOTNET_FRAMEWORK_462_RELEASE:
        raise LauncherError(
            "Short Tracker 需要 Microsoft .NET Framework 4.6.2 或更高版本。\n"
            "Short Tracker requires Microsoft .NET Framework 4.6.2 or later."
        )
    version = _webview2_runtime_version()
    numeric = _numeric_version(version)
    if numeric is None or numeric < WEBVIEW2_MINIMUM_VERSION:
        raise LauncherError(
            "没有检测到可用的 Microsoft Edge WebView2 Runtime。\n"
            "请从 Microsoft 安装 Evergreen WebView2 Runtime 后重试。\n\n"
            "Microsoft Edge WebView2 Runtime was not found. Install the Evergreen "
            f"Runtime and try again.\n\n{WEBVIEW2_DOWNLOAD_URL}"
        )
    return version


def _configure_pythonnet_runtime() -> None:
    """Configure pythonnet before pywebview imports ``clr``.

    A ZIP downloaded from GitHub can retain Windows' Internet-zone marker when
    Explorer extracts it. .NET Framework then refuses to load the bundled
    ``Python.Runtime.dll`` and clr-loader surfaces the misleading
    ``Failed to resolve Python.Runtime.Loader.Initialize`` error. A dedicated
    AppDomain with our reviewed runtime configuration permits those local,
    application-owned managed assemblies without changing or unblocking files
    in the extracted program directory.
    """

    if os.name != "nt":
        return
    if not PYTHONNET_CONFIG_PATH.is_file():
        raise LauncherError(
            "Short Tracker 的 .NET 运行配置缺失。请重新下载并完整解压发布包。\n"
            "Short Tracker's .NET runtime configuration is missing. Download "
            "and fully extract the release again."
        )
    try:
        import pythonnet

        if pythonnet.get_runtime_info() is None:
            pythonnet.set_runtime(
                "netfx",
                domain=PYTHONNET_APPDOMAIN,
                config_file=PYTHONNET_CONFIG_PATH,
            )
    except Exception as exc:
        raise LauncherError(
            "Short Tracker 无法配置 .NET 桌面运行环境。\n"
            "Short Tracker could not configure its .NET desktop runtime."
        ) from exc


def _console_python(path: str | Path) -> str:
    """Prefer python.exe beside pythonw.exe so probes have reliable stdout."""

    candidate = Path(path)
    if candidate.name.casefold() in {"pythonw.exe", "pythonw"}:
        sibling_name = "python.exe" if candidate.suffix.casefold() == ".exe" else "python"
        sibling = candidate.with_name(sibling_name)
        if sibling.is_file():
            return str(sibling)
    return str(candidate)


def _python_candidates(project_root: Path) -> list[PythonCommand]:
    candidates: list[PythonCommand] = []

    configured = os.environ.get("SHORT_TRACKER_PYTHON", "").strip().strip('"')
    if configured:
        candidates.append(PythonCommand((_console_python(configured),), "SHORT_TRACKER_PYTHON"))

    virtual_python = project_root / ".venv" / "Scripts" / "python.exe"
    candidates.append(PythonCommand((str(virtual_python),), "project .venv"))

    if sys.executable:
        candidates.append(PythonCommand((_console_python(sys.executable),), "current Python"))

    path_python = shutil.which("python.exe") or shutil.which("python")
    if path_python:
        candidates.append(PythonCommand((_console_python(path_python),), "PATH python"))

    py_launcher = shutil.which("py.exe") or shutil.which("py")
    if py_launcher:
        candidates.append(PythonCommand((py_launcher, "-3"), "Windows py launcher"))

    unique: list[PythonCommand] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(os.path.normcase(part) for part in candidate.prefix)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _probe_python(candidate: PythonCommand, project_root: Path) -> tuple[bool, str]:
    executable = candidate.prefix[0]
    if ("/" in executable or "\\" in executable) and not Path(executable).is_file():
        return False, f"{candidate.display}: file not found"

    probe = (
        "import json,sys\n"
        "try:\n"
        " import openpyxl\n"
        " dependency=True\n"
        " error=''\n"
        "except Exception as exc:\n"
        " dependency=False\n"
        " error=type(exc).__name__ + ': ' + str(exc)\n"
        "print(json.dumps({'version':list(sys.version_info[:3]),"
        "'executable':sys.executable,'dependency':dependency,'error':error}))\n"
    )
    kwargs: dict[str, Any] = {
        "cwd": str(project_root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 12,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        completed = subprocess.run([*candidate.prefix, "-c", probe], **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{candidate.display}: {type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return False, f"{candidate.display}: {detail[-1] if detail else 'probe failed'}"
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        version = tuple(int(part) for part in payload["version"][:3])
    except (KeyError, TypeError, ValueError, IndexError, json.JSONDecodeError):
        return False, f"{candidate.display}: invalid probe response"
    if version < (3, 11):
        return False, f"{candidate.display}: Python {'.'.join(map(str, version))} is below 3.11"
    if not payload.get("dependency"):
        return False, f"{candidate.display}: openpyxl is unavailable ({payload.get('error', 'unknown')})"
    return True, f"Python {'.'.join(map(str, version))} ({payload.get('executable', executable)})"


def resolve_python(project_root: Path) -> PythonCommand:
    failures: list[str] = []
    for candidate in _python_candidates(project_root):
        valid, detail = _probe_python(candidate, project_root)
        if valid:
            return candidate
        failures.append(detail)
    diagnostics = "\n".join(f"• {line}" for line in failures[-5:])
    raise LauncherError(
        "找不到可用的 Python 3.11+，或尚未安装 openpyxl。\n"
        "No usable Python 3.11+ with openpyxl was found.\n\n"
        "请安装 Python 后在项目目录运行：\n"
        "python -m pip install -r requirements.txt"
        + (f"\n\nDiagnostics:\n{diagnostics}" if diagnostics else "")
    )


def resolve_service_runtime(project_root: Path) -> PythonCommand:
    """Return the command prefix used to launch the local HTTP service."""

    if is_frozen():
        if not sys.executable:
            raise LauncherError("The bundled Short Tracker executable could not be resolved.")
        return PythonCommand(
            (str(Path(sys.executable).resolve()), "--service-child"),
            "bundled Short Tracker runtime",
            True,
        )
    python = resolve_python(project_root)
    return PythonCommand((*python.prefix, "-m", "short_tracker"), python.display)


class ShortTrackerLauncher:
    def __init__(
        self,
        project_root: str | Path = PROJECT_ROOT,
        *,
        data_dir: str | Path | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        start_timeout: float = START_TIMEOUT_SECONDS,
        stop_timeout: float = STOP_TIMEOUT_SECONDS,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.data_dir = (
            Path(data_dir).resolve()
            if data_dir
            else default_data_dir(self.project_root)
        )
        self.host = host
        self.port = int(port)
        self.start_timeout = float(start_timeout)
        self.stop_timeout = float(stop_timeout)
        self.runtime_kind = "frozen" if is_frozen() else "source"
        if self.host != DEFAULT_HOST:
            raise LauncherError("The desktop launcher only supports 127.0.0.1.")
        if not 1024 <= self.port <= 65535:
            raise LauncherError("Port must be between 1024 and 65535.")
        self.runtime_dir = self.data_dir / "runtime"
        self.state_path = self.runtime_dir / f"desktop-service-{self.port}.json"
        self.lock_path = self.runtime_dir / "desktop-launcher.lock"
        self.url = f"http://{self.host}:{self.port}/"
        self.health_url = f"{self.url}api/health"
        self._owned_process: subprocess.Popen[bytes] | None = None

    def _wait_for_owned_process(self, pid: object, *, timeout: float = 2.0) -> bool:
        process = self._owned_process
        if process is None or process.pid != pid:
            return False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        finally:
            if process.poll() is not None:
                self._owned_process = None
        return process.poll() is not None

    def _terminate_fresh_process(self, process: subprocess.Popen[bytes]) -> bool:
        """Terminate only the exact child handle created by this launcher action."""

        if process.poll() is not None:
            return True
        try:
            process.terminate()
            process.wait(timeout=3.0)
        except (OSError, subprocess.TimeoutExpired):
            # Do not escalate to a PID-based kill.  The caller reports that
            # manual diagnosis may be required, and no unrelated PID is used.
            return False
        finally:
            if process.poll() is not None:
                self._owned_process = None
        return process.poll() is not None

    def _health(
        self,
        *,
        timeout: float = 0.7,
        expected_instance_id: str | None = None,
    ) -> dict[str, Any] | None:
        request = Request(
            self.health_url,
            headers={"Accept": "application/json", "User-Agent": "ShortTrackerLauncher/1"},
        )
        # A system HTTP proxy must never intercept a loopback identity check.
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                if response.status != 200:
                    return None
                body = response.read(1024 * 1024)
            payload = json.loads(body.decode("utf-8"))
        except (OSError, HTTPError, URLError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("service") != SERVICE_NAME or payload.get("mode") != SERVICE_MODE:
            return None
        if expected_instance_id is not None and payload.get("instance_id") != expected_instance_id:
            return None
        return payload

    def _port_is_open(self, *, timeout: float = 0.35) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def _read_state(self) -> dict[str, Any] | None:
        return _read_json_object(self.state_path)

    def _valid_state(self, state: dict[str, Any] | None) -> tuple[bool, str]:
        if not state:
            return False, "missing state"
        if state.get("schema") != STATE_SCHEMA:
            return False, "unsupported state schema"
        if state.get("service") != SERVICE_NAME or state.get("mode") != SERVICE_MODE:
            return False, "state belongs to another application"
        if state.get("protocol") != SERVICE_PROTOCOL:
            return False, "unsupported service protocol"
        if state.get("runtime_kind") != self.runtime_kind:
            return False, "state belongs to another runtime kind"
        if self.runtime_kind == "source" and not _same_path(
            state.get("project_root", ""), self.project_root
        ):
            return False, "state belongs to another project folder"
        if not _same_path(state.get("data_dir", ""), self.data_dir):
            return False, "state belongs to another data folder"
        if state.get("host") != self.host or state.get("port") != self.port:
            return False, "state belongs to another address"
        pid = state.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False, "invalid process id"
        process_identity = state.get("process_identity")
        if not isinstance(process_identity, dict):
            return False, "missing process identity"
        if process_identity.get("pid") != pid:
            return False, "process identity PID mismatch"
        if not isinstance(process_identity.get("executable"), str) or not process_identity.get(
            "executable"
        ):
            return False, "missing process executable"
        if not isinstance(process_identity.get("creation_id"), str) or not process_identity.get(
            "creation_id"
        ):
            return False, "missing process creation marker"
        try:
            token = str(uuid.UUID(str(state.get("shutdown_token", ""))))
        except (ValueError, AttributeError):
            return False, "invalid shutdown token"
        if token != state.get("shutdown_token"):
            return False, "non-canonical shutdown token"
        try:
            instance_id = str(uuid.UUID(str(state.get("instance_id", ""))))
        except (ValueError, AttributeError):
            return False, "invalid instance id"
        if instance_id != state.get("instance_id"):
            return False, "non-canonical instance id"
        return True, "ok"

    def _state_process_status(self, state: dict[str, Any]) -> str:
        expected = state.get("process_identity")
        pid = state.get("pid")
        if not isinstance(expected, dict) or not isinstance(pid, int):
            return "unknown"
        status, current = _query_process_identity(pid)
        if status != "alive" or current is None:
            return status
        if current.get("creation_id") != expected.get("creation_id"):
            return "mismatch"
        if not _same_path(str(current.get("executable", "")), str(expected.get("executable", ""))):
            return "mismatch"
        return "match"

    def _remove_state(self, token: str | None = None) -> None:
        current = self._read_state()
        if token is not None and current and current.get("shutdown_token") != token:
            return
        self.state_path.unlink(missing_ok=True)

    def _stop_path(self, token: str) -> Path:
        canonical = str(uuid.UUID(token))
        return self.runtime_dir / f"stop-{canonical}.request"

    def _write_stop_request(self, token: str, *, force: bool) -> Path:
        stop_path = self._stop_path(token)
        _atomic_write_json(
            stop_path,
            {
                "schema": 1,
                "force": bool(force),
                "requested_at_utc": _utc_now(),
            },
        )
        return stop_path

    @staticmethod
    def _tail(path: Path, *, limit: int = 5000) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit))
                return handle.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _open_browser(self) -> bool:
        try:
            return bool(webbrowser.open(self.url, new=2))
        except Exception:
            return False

    def start(
        self,
        *,
        open_browser: bool = True,
        skip_startup_sync: bool = False,
    ) -> ActionResult:
        try:
            with _runtime_lock(self.lock_path):
                health = self._health()
                if health is not None:
                    browser_opened = self._open_browser() if open_browser else None
                    suffix = (
                        f"\n浏览器未能自动打开，请使用此地址 / Open manually: {self.url}"
                        if browser_opened is False
                        else ""
                    )
                    existing_state = self._read_state()
                    state_valid, _ = self._valid_state(existing_state)
                    managed_instance = bool(
                        state_valid
                        and existing_state
                        and health.get("instance_id") == existing_state.get("instance_id")
                    )
                    if not managed_instance:
                        message = (
                            "检测到旧版前台或外部启动的 Short Tracker，现有页面已打开。\n"
                            "若要改用无黑框后台模式，请先关闭原来的命令窗口一次，再重新启动。\n"
                            "An older foreground/external instance is running. Close its original "
                            "console once before switching to background mode."
                        )
                    elif open_browser:
                        message = (
                            "Short Tracker 已经在运行，已打开现有页面。\n"
                            "Short Tracker is already running; the existing page was opened."
                        )
                    else:
                        message = (
                            "Short Tracker 已经在运行。\n"
                            "Short Tracker is already running."
                        )
                    return ActionResult(
                        True,
                        message + suffix,
                        url=self.url,
                        already_running=True,
                        attention=not managed_instance or browser_opened is False,
                        browser_opened=browser_opened,
                    )

                if self._port_is_open():
                    return ActionResult(
                        False,
                        f"端口 {self.port} 已被其他程序占用；为安全起见未启动或关闭任何进程。\n"
                        f"Port {self.port} is occupied by a different service. Nothing was started or stopped.",
                        url=self.url,
                    )

                runtime = resolve_service_runtime(self.project_root)
                self.runtime_dir.mkdir(parents=True, exist_ok=True)
                logs_dir = self.runtime_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                run_id = uuid.uuid4().hex[:8]
                stdout_path = logs_dir / f"{stamp}-{run_id}.out.log"
                stderr_path = logs_dir / f"{stamp}-{run_id}.err.log"
                shutdown_token = str(uuid.uuid4())
                instance_id = str(uuid.uuid4())
                # Remove the request for this fresh UUID before spawning.  The
                # service itself must never delete a request that may have been
                # written after Popen returned.
                self._stop_path(shutdown_token).unlink(missing_ok=True)
                command = [*runtime.prefix]
                if runtime.bundled:
                    command.extend(
                        [
                            "--stdout-log",
                            str(stdout_path),
                            "--stderr-log",
                            str(stderr_path),
                        ]
                    )
                command.extend(
                    [
                    "--data-dir",
                    str(self.data_dir),
                    "serve",
                    "--host",
                    self.host,
                    "--port",
                    str(self.port),
                    "--shutdown-token",
                    shutdown_token,
                    "--instance-id",
                    instance_id,
                    ]
                )
                if skip_startup_sync:
                    command.append("--skip-startup-sync")

                environment = os.environ.copy()
                environment["PYTHONUTF8"] = "1"
                environment["PYTHONUNBUFFERED"] = "1"
                if runtime.bundled:
                    # PyInstaller requires a reset when the child must outlive
                    # this launcher process; otherwise it may reuse the
                    # parent's temporary/bundle environment.
                    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                popen_kwargs: dict[str, Any] = {
                    "cwd": str(self.project_root),
                    "env": environment,
                    "stdin": subprocess.DEVNULL,
                    "close_fds": True,
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = getattr(
                        subprocess, "CREATE_NO_WINDOW", 0x08000000
                    )
                else:
                    popen_kwargs["start_new_session"] = True

                stdout_handle = stdout_path.open("ab", buffering=0)
                stderr_handle = stderr_path.open("ab", buffering=0)
                try:
                    process = subprocess.Popen(
                        command,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        **popen_kwargs,
                    )
                    self._owned_process = process
                except OSError as exc:
                    return ActionResult(
                        False,
                        f"后台服务无法启动：{exc}\nThe background service could not start.",
                        log_path=stderr_path,
                    )
                finally:
                    stdout_handle.close()
                    stderr_handle.close()

                process_status, process_identity = _query_process_identity(process.pid)
                if process_status != "alive" or process_identity is None:
                    stopped = self._terminate_fresh_process(process)
                    return ActionResult(
                        False,
                        "无法建立可验证的后台进程身份。"
                        + (
                            "本次新建进程已终止。\n"
                            if stopped
                            else "无法确认本次进程已退出。\n"
                        )
                        + "The launcher could not establish a verifiable process identity.",
                        log_path=stderr_path,
                    )

                state = {
                    "schema": STATE_SCHEMA,
                    "service": SERVICE_NAME,
                    "mode": SERVICE_MODE,
                    "protocol": SERVICE_PROTOCOL,
                    "version": __version__,
                    "runtime_kind": self.runtime_kind,
                    "status": "starting",
                    "pid": process.pid,
                    "process_identity": process_identity,
                    "started_at_utc": _utc_now(),
                    "project_root": str(self.project_root),
                    "data_dir": str(self.data_dir),
                    "host": self.host,
                    "port": self.port,
                    "url": self.url,
                    "runtime_command": list(runtime.prefix),
                    "shutdown_token": shutdown_token,
                    "instance_id": instance_id,
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                }
                try:
                    _atomic_write_json(self.state_path, state)
                except OSError as exc:
                    # Without durable state the normal stop tool could not
                    # authenticate this instance.  This is the exact Popen
                    # handle just created above, so terminating it cannot hit
                    # an unrelated process or a reused PID.
                    try:
                        self._write_stop_request(shutdown_token, force=True)
                    except OSError:
                        pass
                    self._wait_for_owned_process(process.pid, timeout=2.0)
                    stopped = self._terminate_fresh_process(process)
                    if stopped:
                        self._stop_path(shutdown_token).unlink(missing_ok=True)
                    return ActionResult(
                        False,
                        f"无法保存后台服务状态：{exc}\n"
                        + (
                            "已终止本次新建进程。\nThe newly created process was terminated."
                            if stopped
                            else "无法确认本次进程已退出，请查看日志和任务管理器。\n"
                            "The launcher could not confirm that the new process exited."
                        ),
                        log_path=stderr_path,
                    )

                deadline = time.monotonic() + self.start_timeout
                while time.monotonic() < deadline:
                    exit_code = process.poll()
                    if exit_code is not None:
                        detail = self._tail(stderr_path) or self._tail(stdout_path)
                        self._remove_state(shutdown_token)
                        return ActionResult(
                            False,
                            f"Short Tracker 启动失败（退出代码 {exit_code}）。\n"
                            f"Short Tracker failed to start (exit code {exit_code})."
                            + (f"\n\n{detail[-1800:]}" if detail else ""),
                            log_path=stderr_path,
                        )
                    health = self._health(expected_instance_id=instance_id)
                    if health is not None:
                        state["status"] = "running"
                        state["ready_at_utc"] = _utc_now()
                        try:
                            _atomic_write_json(self.state_path, state)
                        except OSError:
                            # The initial state already contains every value
                            # required for authenticated stopping.  A cosmetic
                            # status update must not turn a healthy start into
                            # an unmanaged error.
                            pass
                        browser_opened = self._open_browser() if open_browser else None
                        suffix = (
                            f"\n浏览器未能自动打开，请使用此地址 / Open manually: {self.url}"
                            if browser_opened is False
                            else ""
                        )
                        return ActionResult(
                            True,
                            "Short Tracker 已在后台启动。\nShort Tracker is running in the background."
                            + suffix,
                            url=self.url,
                            log_path=stderr_path,
                            attention=browser_opened is False,
                            browser_opened=browser_opened,
                        )
                    time.sleep(0.2)

                # Only the process created in this action knows this UUID.  A
                # timeout therefore requests its graceful shutdown without
                # ever terminating an unrelated PID.
                try:
                    self._write_stop_request(shutdown_token, force=True)
                except OSError:
                    pass
                self._wait_for_owned_process(process.pid, timeout=self.stop_timeout)
                stopped = process.poll() is not None
                if not stopped:
                    stopped = self._terminate_fresh_process(process)
                if stopped:
                    self._stop_path(shutdown_token).unlink(missing_ok=True)
                    self._remove_state(shutdown_token)
                else:
                    state["status"] = "unhealthy_shutdown_requested"
                    try:
                        _atomic_write_json(self.state_path, state)
                    except OSError:
                        pass
                detail = self._tail(stderr_path) or self._tail(stdout_path)
                return ActionResult(
                    False,
                    f"Short Tracker 未能在 {self.start_timeout:g} 秒内通过健康检查。"
                    + (
                        "本次新建进程已停止。\n"
                        if stopped
                        else "停止请求已保留，但无法确认进程退出；运行状态文件未删除。\n"
                    )
                    + f"Short Tracker did not pass its health check within {self.start_timeout:g} seconds; "
                    + (
                        "the newly created process stopped."
                        if stopped
                        else "the shutdown request and runtime state were retained for diagnosis."
                    )
                    + (f"\n\n{detail[-1800:]}" if detail else ""),
                    log_path=stderr_path,
                )
        except LauncherError as exc:
            return ActionResult(False, str(exc))
        except OSError as exc:
            return ActionResult(
                False,
                f"启动器无法访问所需的本地文件：{exc}\n"
                "The launcher could not access a required local file.",
            )

    def stop(self, *, force_during_sync: bool = False) -> ActionResult:
        try:
            with _runtime_lock(self.lock_path):
                state = self._read_state()
                health = self._health()

                if health is None:
                    if self._port_is_open():
                        return ActionResult(
                            False,
                            f"端口 {self.port} 上不是可验证的 Short Tracker；为安全起见没有停止任何进程。\n"
                            f"Port {self.port} does not expose a verified Short Tracker service. "
                            "Nothing was stopped.",
                        )
                    valid, reason = self._valid_state(state)
                    if valid:
                        token = str(state["shutdown_token"])
                        process_status = self._state_process_status(state)
                        if process_status == "match":
                            try:
                                self._write_stop_request(
                                    token,
                                    force=force_during_sync,
                                )
                            except OSError as exc:
                                return ActionResult(
                                    False,
                                    f"服务尚未响应，且无法保存停止请求：{exc}\n"
                                    "The service is not responding and its stop request could not be saved.",
                                )
                            return ActionResult(
                                False,
                                "后台进程仍存在，但健康接口尚未就绪；停止请求和运行状态已保留。\n"
                                "请稍后再次运行停止器确认结果。\n"
                                "The background process still exists but its health endpoint is not ready. "
                                "The stop request and runtime state were retained; run Stop again shortly.",
                            )
                        if process_status == "unknown":
                            return ActionResult(
                                False,
                                "无法确认记录中的后台进程是否已经退出；为安全起见保留运行状态，"
                                "且没有结束任何 PID。\n"
                                "The recorded process could not be verified. Runtime state was retained, "
                                "and no PID was terminated.",
                            )
                        self._stop_path(token).unlink(missing_ok=True)
                        self._remove_state(token)
                    elif state is not None:
                        return ActionResult(
                            False,
                            f"运行状态文件无效（{reason}）；没有删除状态或结束任何进程。\n"
                            "The runtime state is invalid. No state or process was removed.",
                        )
                    return ActionResult(
                        True,
                        "Short Tracker 已经停止。\nShort Tracker is already stopped.",
                    )

                valid, reason = self._valid_state(state)
                managed_instance = bool(
                    valid and state and health.get("instance_id") == state.get("instance_id")
                )
                if not managed_instance:
                    if valid:
                        reason = "health response belongs to another launcher instance"
                    return ActionResult(
                        False,
                        "检测到 Short Tracker 正在运行，但它不是由当前后台启动器管理的实例。\n"
                        "请关闭原来的命令窗口；之后再使用新的启动图标。\n"
                        "A Short Tracker service is running, but it was not started by this "
                        f"background launcher ({reason}). Close its original console window first.",
                        url=self.url,
                    )

                sync = health.get("sync")
                sync_running = isinstance(sync, dict) and sync.get("running") is True
                if sync_running and not force_during_sync:
                    return ActionResult(
                        False,
                        "FCA 数据同步仍在运行，为保护本地数据库，本次没有停止服务。\n"
                        "等待页面显示同步完成后再停止。\n"
                        "An FCA sync is still running. The service was left running to protect "
                        "the local database; try again when the sync finishes.",
                        url=self.url,
                    )

                token = str(state["shutdown_token"])
                try:
                    stop_path = self._write_stop_request(
                        token,
                        force=force_during_sync,
                    )
                except OSError as exc:
                    return ActionResult(
                        False,
                        f"无法写入安全停止请求：{exc}\nCould not write the shutdown request.",
                    )

                deadline = time.monotonic() + self.stop_timeout
                while time.monotonic() < deadline:
                    if not self._port_is_open(timeout=0.2):
                        self._wait_for_owned_process(state.get("pid"))
                        stop_path.unlink(missing_ok=True)
                        self._remove_state(token)
                        return ActionResult(
                            True,
                            "Short Tracker 已安全停止。\nShort Tracker stopped safely.",
                        )
                    if not stop_path.exists():
                        current = self._health(
                            timeout=0.4,
                            expected_instance_id=str(state["instance_id"]),
                        )
                        if current is not None:
                            return ActionResult(
                                False,
                                "停止确认前有新的 FCA 同步开始，服务端已拒绝本次普通停止请求。\n"
                                "等待同步完成后再试。\n"
                                "A new FCA sync began before shutdown was committed. The service "
                                "rejected this normal stop request; try again after the sync finishes.",
                                url=self.url,
                            )
                    time.sleep(0.2)

                return ActionResult(
                    False,
                    f"服务未在 {self.stop_timeout:g} 秒内停止。没有强行终止任何进程；请查看日志后重试。\n"
                    f"The service did not stop within {self.stop_timeout:g} seconds. No process was force-terminated; "
                    "review the logs and try again.",
                    log_path=Path(str(state.get("stderr_log", "")))
                    if state.get("stderr_log")
                    else None,
                )
        except LauncherError as exc:
            return ActionResult(False, str(exc))
        except OSError as exc:
            return ActionResult(
                False,
                f"停止器无法访问所需的本地文件：{exc}\n"
                "The stop tool could not access a required local file.",
            )

    def status(self) -> ActionResult:
        health = self._health()
        if health is not None:
            state = self._read_state()
            valid, _ = self._valid_state(state)
            managed_instance = bool(
                valid and state and health.get("instance_id") == state.get("instance_id")
            )
            qualifier = "后台启动器管理" if managed_instance else "外部/前台实例"
            return ActionResult(
                True,
                f"Short Tracker 正在运行（{qualifier}）。\nShort Tracker is running.",
                url=self.url,
                already_running=True,
            )
        if self._port_is_open():
            return ActionResult(False, f"Port {self.port} is occupied by another service.")
        state = self._read_state()
        valid, reason = self._valid_state(state)
        if valid and state:
            process_status = self._state_process_status(state)
            if process_status == "match":
                return ActionResult(
                    True,
                    "Short Tracker 后台进程仍存在，正在启动、停止或等待健康接口。\n"
                    "The Short Tracker process exists but its health endpoint is not ready.",
                    already_running=True,
                )
            if process_status == "unknown":
                return ActionResult(
                    False,
                    "Short Tracker 的运行状态存在，但无法验证其中的进程。\n"
                    "Runtime state exists, but its process cannot be verified.",
                )
        elif state is not None:
            return ActionResult(False, f"Short Tracker runtime state is invalid ({reason}).")
        return ActionResult(True, "Short Tracker 已停止。\nShort Tracker is stopped.")


def _native_message(message: str, *, flags: int = 0) -> int:
    if os.name == "nt":
        try:
            import ctypes

            return int(ctypes.windll.user32.MessageBoxW(0, message, "Short Tracker", flags))
        except Exception:
            pass
    stream = sys.stderr or sys.__stderr__
    if stream is not None:
        stream.write(message + "\n")
    return 0


class DesktopBridge:
    """Minimal native API exposed to the trusted loopback dashboard."""

    def __init__(self, opener: Callable[..., object] | None = None) -> None:
        self._opener = opener or webbrowser.open

    @staticmethod
    def _github_url(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate or len(candidate) > 2048 or any(ord(char) < 32 for char in candidate):
            return None
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").rstrip(".").casefold() != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or not parsed.path.startswith("/")
        ):
            return None
        return parsed.geturl()

    def open_external(self, url: object) -> dict[str, object]:
        """Open only a validated HTTPS GitHub URL in the system browser."""

        candidate = self._github_url(url)
        if candidate is None:
            return {"ok": False, "error": "Only https://github.com/ links are allowed."}
        try:
            opened = bool(self._opener(candidate, new=2))
        except Exception:
            opened = False
        return {"ok": opened, "url": candidate if opened else None}


def _sync_is_running(health: dict[str, Any] | None) -> bool:
    sync = health.get("sync") if isinstance(health, dict) else None
    return isinstance(sync, dict) and sync.get("running") is True


def _wait_for_safe_stop(
    launcher: ShortTrackerLauncher,
    *,
    on_status: Callable[[str], None] | None = None,
    timeout: float = DESKTOP_CLOSE_SYNC_TIMEOUT_SECONDS,
    poll_interval: float = DESKTOP_CLOSE_POLL_SECONDS,
) -> ActionResult:
    """Wait for an active sync and then use the authenticated graceful stop."""

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        health = launcher._health()
        if _sync_is_running(health):
            if on_status is not None:
                on_status(
                    "Short Tracker — 正在完成数据同步，完成后自动关闭… / "
                    "Finishing data sync before closing…"
                )
            if time.monotonic() >= deadline:
                return ActionResult(
                    False,
                    "FCA 数据同步在等待期限内没有完成；为保护本地数据库，程序保持运行。\n"
                    "The FCA sync did not finish within the safe-close timeout. "
                    "Short Tracker was left running to protect the local database.",
                    url=launcher.url,
                )
            time.sleep(max(0.01, poll_interval))
            continue

        if on_status is not None:
            on_status("Short Tracker — 正在安全关闭… / Closing safely…")
        result = launcher.stop()
        if result.ok:
            return result

        # A sync can start between the health observation and the authenticated
        # stop request.  Wait for that sync instead of escalating to a kill.
        if _sync_is_running(launcher._health()) and time.monotonic() < deadline:
            time.sleep(max(0.01, poll_interval))
            continue
        return result


class DesktopSession:
    """Bind one WebView2 window to one verified Short Tracker service."""

    def __init__(
        self,
        launcher: ShortTrackerLauncher,
        window: Any,
        *,
        notifier: Callable[..., int] = _native_message,
        close_timeout: float = DESKTOP_CLOSE_SYNC_TIMEOUT_SECONDS,
        poll_interval: float = DESKTOP_CLOSE_POLL_SECONDS,
    ) -> None:
        self.launcher = launcher
        self.window = window
        self.notifier = notifier
        self.close_timeout = float(close_timeout)
        self.poll_interval = float(poll_interval)
        self._state_lock = threading.Lock()
        self._closing = False
        self._allow_close = False
        self._shutdown_thread: threading.Thread | None = None
        self._shutdown_complete = threading.Event()
        self.last_stop_result: ActionResult | None = None

    def _set_title(self, title: str) -> None:
        try:
            self.window.set_title(title)
        except Exception:
            pass

    def on_closing(self) -> bool | None:
        """Cancel the first close event until the backend stops safely."""

        with self._state_lock:
            if self._allow_close:
                return None
            if self._closing:
                return False
            self._closing = True
            self._set_title("Short Tracker — 正在安全关闭… / Closing safely…")
            self._shutdown_thread = threading.Thread(
                target=self._close_worker,
                name="short-tracker-desktop-close",
                daemon=False,
            )
            self._shutdown_thread.start()
        return False

    def _close_worker(self) -> None:
        result = _wait_for_safe_stop(
            self.launcher,
            on_status=self._set_title,
            timeout=self.close_timeout,
            poll_interval=self.poll_interval,
        )
        self.last_stop_result = result
        if result.ok:
            with self._state_lock:
                self._allow_close = True
            self._shutdown_complete.set()
            try:
                self.window.destroy()
            except Exception:
                pass
            return

        with self._state_lock:
            self._closing = False
            self._shutdown_thread = None
        self._set_title("Short Tracker")
        self.notifier(
            "Short Tracker 尚未关闭。\n\n"
            "Short Tracker is still running.\n\n"
            f"{result.message}",
            flags=0x10,
        )

    def ensure_stopped_after_gui(self) -> ActionResult:
        """Clean up if the GUI loop exits without the normal close callback."""

        with self._state_lock:
            worker = self._shutdown_thread
        if worker is not None:
            worker.join()
            if self.last_stop_result is not None:
                return self.last_stop_result

        if self._shutdown_complete.is_set() and self.last_stop_result is not None:
            return self.last_stop_result
        result = _wait_for_safe_stop(
            self.launcher,
            timeout=self.close_timeout,
            poll_interval=self.poll_interval,
        )
        self.last_stop_result = result
        if result.ok:
            self._shutdown_complete.set()
        return result


def _configure_webview(webview_module: Any) -> None:
    settings = getattr(webview_module, "settings", None)
    if settings is None:
        return
    settings["ALLOW_DOWNLOADS"] = False
    settings["ALLOW_FILE_URLS"] = False
    # External navigation must go through DesktopBridge.open_external(), where
    # the URL is reduced to a strict HTTPS GitHub allow-list.
    settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    settings["REMOTE_DEBUGGING_PORT"] = None
    settings["SHOW_DEFAULT_MENUS"] = False


def run_desktop(
    *,
    launcher: ShortTrackerLauncher | None = None,
    webview_module: Any | None = None,
    check_prerequisites: bool = True,
    skip_startup_sync: bool = False,
) -> int:
    """Run the single visible WebView2 window for the local dashboard."""

    desktop_launcher = launcher or ShortTrackerLauncher()
    window_lock_path = desktop_launcher.runtime_dir / "desktop-window.lock"
    try:
        with _desktop_window_lock(window_lock_path):
            if check_prerequisites:
                _require_windows_webview2()
            if webview_module is None:
                try:
                    _configure_pythonnet_runtime()
                    import webview as webview_module
                except LauncherError:
                    raise
                except ImportError as exc:
                    raise LauncherError(
                        "Short Tracker 桌面组件未正确安装。\n"
                        "The Short Tracker desktop component is unavailable."
                    ) from exc
            _configure_webview(webview_module)

            started = desktop_launcher.start(
                open_browser=False,
                skip_startup_sync=skip_startup_sync,
            )
            if not started.ok:
                _native_message(started.message, flags=0x10)
                return 1
            if started.already_running and started.attention:
                # Do not attach a desktop lifetime to an unmanaged/legacy
                # service that this launcher cannot later stop safely.
                _native_message(started.message, flags=0x30)
                return 1

            session: DesktopSession | None = None
            try:
                storage_path = desktop_launcher.data_dir / "webview-profile"
                storage_path.mkdir(parents=True, exist_ok=True)
                bridge = DesktopBridge()
                window = webview_module.create_window(
                    "Short Tracker",
                    started.url or desktop_launcher.url,
                    js_api=bridge,
                    width=1380,
                    height=900,
                    min_size=(960, 640),
                    resizable=True,
                    background_color="#0d1728",
                    text_select=True,
                    zoomable=True,
                    confirm_close=False,
                )
                if window is None:
                    raise LauncherError("The WebView2 desktop window could not be created.")
                session = DesktopSession(desktop_launcher, window)
                window.events.closing += session.on_closing
                # Explicitly force Edge Chromium.  The prerequisite check above
                # prevents pywebview from silently falling back to MSHTML.
                webview_module.start(
                    gui="edgechromium",
                    debug=False,
                    private_mode=False,
                    storage_path=str(storage_path),
                )
            except Exception as exc:
                cleanup = (
                    session.ensure_stopped_after_gui()
                    if session is not None
                    else _wait_for_safe_stop(desktop_launcher)
                )
                detail = (
                    f"\n\nService cleanup: {cleanup.message}" if not cleanup.ok else ""
                )
                _native_message(
                    "Short Tracker 桌面窗口无法启动。\n\n"
                    "The Short Tracker desktop window could not start.\n\n"
                    f"{type(exc).__name__}: {exc}{detail}",
                    flags=0x10,
                )
                return 1

            stopped = session.ensure_stopped_after_gui()
            if not stopped.ok:
                _native_message(stopped.message, flags=0x10)
                return 1
            return 0
    except DesktopAlreadyRunningError as exc:
        _native_message(str(exc), flags=0x40)
        return 0
    except LauncherError as exc:
        _native_message(str(exc), flags=0x10)
        return 1
    except OSError as exc:
        _native_message(
            f"Short Tracker 无法访问桌面运行状态：{exc}\n"
            "Short Tracker could not access its desktop runtime state.",
            flags=0x10,
        )
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the Short Tracker background service.")
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--skip-startup-sync", action="store_true")
    parser.add_argument(
        "--force-during-sync",
        action="store_true",
        help="request shutdown even while an FCA sync is running",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        launcher = ShortTrackerLauncher(data_dir=args.data_dir, port=args.port)
    except LauncherError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.action == "start":
        result = launcher.start(
            open_browser=not args.no_open,
            skip_startup_sync=args.skip_startup_sync,
        )
    elif args.action == "stop":
        result = launcher.stop(force_during_sync=args.force_during_sync)
    else:
        result = launcher.status()
    stream = sys.stdout if result.ok else sys.stderr
    print(result.message, file=stream)
    if result.url:
        print(result.url, file=stream)
    if result.log_path and not result.ok:
        print(f"Log: {result.log_path}", file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
