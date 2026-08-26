from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from launcher.desktop_launcher import (
    ActionResult,
    DesktopBridge,
    DesktopSession,
    SERVICE_MODE,
    SERVICE_NAME,
    SERVICE_PROTOCOL,
    STATE_SCHEMA,
    ShortTrackerLauncher,
    _desktop_window_lock,
    _numeric_version,
    _wait_for_safe_stop,
    resolve_service_runtime,
    run_desktop,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self):
        return [handler() for handler in list(self.handlers)]


class FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(closing=FakeEvent())
        self.titles: list[str] = []
        self.destroyed = threading.Event()

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def destroy(self) -> None:
        # A real pywebview destroy emits closing a second time. The session's
        # committed-close branch must allow that event to proceed.
        self.events.closing.emit()
        self.destroyed.set()


class FakeWebview:
    def __init__(self) -> None:
        self.settings = {}
        self.window = FakeWindow()
        self.create_kwargs = None
        self.start_kwargs = None

    def create_window(self, *args, **kwargs):
        self.create_kwargs = (args, kwargs)
        return self.window

    def start(self, **kwargs) -> None:
        self.start_kwargs = kwargs
        cancelled = self.window.events.closing.emit()
        if False not in cancelled:
            raise AssertionError("desktop close was not held for safe shutdown")
        if not self.window.destroyed.wait(2):
            raise AssertionError("desktop close worker did not destroy the window")


class FakeDesktopLauncher:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.runtime_dir = data_dir / "runtime"
        self.url = "http://127.0.0.1:54321/"
        self.start_calls: list[dict[str, object]] = []
        self.stop_calls = 0
        self.health_values: list[dict[str, object] | None] = []

    def start(self, **kwargs) -> ActionResult:
        self.start_calls.append(kwargs)
        return ActionResult(True, "started", url=self.url)

    def stop(self, **kwargs) -> ActionResult:
        self.stop_calls += 1
        return ActionResult(True, "stopped")

    def _health(self, **kwargs):
        if self.health_values:
            return self.health_values.pop(0)
        return {
            "service": SERVICE_NAME,
            "mode": SERVICE_MODE,
            "sync": {"running": False},
        }


class DesktopWindowTests(unittest.TestCase):
    def test_external_bridge_allows_only_https_github(self) -> None:
        opened: list[tuple[str, int]] = []

        def opener(url: str, *, new: int) -> bool:
            opened.append((url, new))
            return True

        bridge = DesktopBridge(opener)
        allowed = bridge.open_external(
            "https://github.com/example/short-tracker/releases/tag/v0.2.0"
        )
        self.assertTrue(allowed["ok"])
        self.assertEqual(opened, [(allowed["url"], 2)])

        rejected = (
            "http://github.com/example/release",
            "https://github.com.evil.example/release",
            "https://user@github.com/example/release",
            "https://github.com:444/example/release",
            "file:" + "///C:/Windows/System32/calc.exe",
            "javascript:alert(1)",
            "http://127.0.0.1:8777/",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(bridge.open_external(url)["ok"])
        self.assertEqual(len(opened), 1)

    def test_version_parser_rejects_non_numeric_runtime_versions(self) -> None:
        self.assertEqual(_numeric_version("151.0.4129.101"), (151, 0, 4129, 101))
        self.assertIsNone(_numeric_version("151.beta"))
        self.assertIsNone(_numeric_version(""))

    def test_safe_stop_waits_for_sync_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            launcher = FakeDesktopLauncher(Path(temporary))
            launcher.health_values = [
                {"sync": {"running": True}},
                {"sync": {"running": True}},
                {"sync": {"running": False}},
            ]
            statuses: list[str] = []
            result = _wait_for_safe_stop(
                launcher,
                on_status=statuses.append,
                timeout=1,
                poll_interval=0.001,
            )
        self.assertTrue(result.ok)
        self.assertEqual(launcher.stop_calls, 1)
        self.assertTrue(any("Finishing data sync" in status for status in statuses))

    def test_window_close_waits_then_destroys_after_safe_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            launcher = FakeDesktopLauncher(Path(temporary))
            launcher.health_values = [
                {"sync": {"running": True}},
                {"sync": {"running": False}},
            ]
            window = FakeWindow()
            session = DesktopSession(
                launcher,
                window,
                close_timeout=1,
                poll_interval=0.001,
            )
            window.events.closing += session.on_closing
            self.assertIn(False, window.events.closing.emit())
            self.assertTrue(window.destroyed.wait(2))
            result = session.ensure_stopped_after_gui()
        self.assertTrue(result.ok)
        self.assertEqual(launcher.stop_calls, 1)
        self.assertTrue(any("Finishing data sync" in title for title in window.titles))

    def test_desktop_uses_one_webview_and_never_opens_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            launcher = FakeDesktopLauncher(Path(temporary))
            webview = FakeWebview()
            with patch("launcher.desktop_launcher._native_message") as message:
                exit_code = run_desktop(
                    launcher=launcher,
                    webview_module=webview,
                    check_prerequisites=False,
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            launcher.start_calls,
            [{"open_browser": False, "skip_startup_sync": False}],
        )
        self.assertEqual(launcher.stop_calls, 1)
        self.assertEqual(webview.start_kwargs["gui"], "edgechromium")
        self.assertFalse(webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"])
        self.assertIsInstance(webview.create_kwargs[1]["js_api"], DesktopBridge)
        message.assert_not_called()

    def test_second_desktop_instance_does_not_start_another_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            launcher = FakeDesktopLauncher(Path(temporary))
            lock_path = launcher.runtime_dir / "desktop-window.lock"
            with (
                _desktop_window_lock(lock_path),
                patch("launcher.desktop_launcher._native_message") as message,
            ):
                exit_code = run_desktop(
                    launcher=launcher,
                    webview_module=FakeWebview(),
                    check_prerequisites=False,
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(launcher.start_calls, [])
        message.assert_called_once()


class DesktopLauncherSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.launcher = ShortTrackerLauncher(
            PROJECT_ROOT,
            data_dir=self.data_dir,
            port=available_port(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def valid_state(self) -> dict[str, object]:
        return {
            "schema": STATE_SCHEMA,
            "service": SERVICE_NAME,
            "mode": SERVICE_MODE,
            "protocol": SERVICE_PROTOCOL,
            "version": "0.1.0",
            "runtime_kind": self.launcher.runtime_kind,
            "pid": os.getpid(),
            "process_identity": {
                "pid": os.getpid(),
                "executable": str(Path(__file__).resolve()),
                "creation_id": "test-creation-id",
            },
            "project_root": str(PROJECT_ROOT),
            "data_dir": str(self.data_dir),
            "host": self.launcher.host,
            "port": self.launcher.port,
            "shutdown_token": str(uuid.uuid4()),
            "instance_id": str(uuid.uuid4()),
        }

    def test_foreign_service_on_port_is_never_started_or_stopped(self) -> None:
        with (
            patch.object(self.launcher, "_health", return_value=None),
            patch.object(self.launcher, "_port_is_open", return_value=True),
            patch("launcher.desktop_launcher.resolve_python") as resolve,
        ):
            result = self.launcher.start(open_browser=False)
        self.assertFalse(result.ok)
        self.assertIn("occupied", result.message)
        resolve.assert_not_called()

        with (
            patch.object(self.launcher, "_health", return_value=None),
            patch.object(self.launcher, "_port_is_open", return_value=True),
        ):
            result = self.launcher.stop()
        self.assertFalse(result.ok)
        self.assertIn("Nothing was stopped", result.message)

    def test_frozen_runtime_launches_the_bundled_executable(self) -> None:
        bundled_executable = PROJECT_ROOT / "Short Tracker.exe"
        with (
            patch("launcher.desktop_launcher.is_frozen", return_value=True),
            patch("launcher.desktop_launcher.sys.executable", str(bundled_executable)),
            patch("launcher.desktop_launcher.resolve_python") as resolve,
        ):
            runtime = resolve_service_runtime(PROJECT_ROOT)
        self.assertEqual(
            runtime.prefix,
            (str(bundled_executable.resolve()), "--service-child"),
        )
        resolve.assert_not_called()

    def test_source_runtime_uses_the_resolved_python_module_command(self) -> None:
        with (
            patch("launcher.desktop_launcher.is_frozen", return_value=False),
            patch("launcher.desktop_launcher.resolve_python") as resolve,
        ):
            resolve.return_value.prefix = ("python.exe",)
            resolve.return_value.display = "test Python"
            runtime = resolve_service_runtime(PROJECT_ROOT)
        self.assertEqual(runtime.prefix, ("python.exe", "-m", "short_tracker"))

    def test_duplicate_start_reuses_verified_service(self) -> None:
        health = {"service": SERVICE_NAME, "mode": SERVICE_MODE, "sync": {"running": False}}
        with (
            patch.object(self.launcher, "_health", return_value=health),
            patch.object(self.launcher, "_open_browser", return_value=True) as browser,
            patch("launcher.desktop_launcher.resolve_python") as resolve,
        ):
            result = self.launcher.start()
        self.assertTrue(result.ok)
        self.assertTrue(result.already_running)
        self.assertTrue(result.attention)
        browser.assert_called_once_with()
        resolve.assert_not_called()

    def test_matching_instance_is_a_managed_duplicate(self) -> None:
        state = self.valid_state()
        self.launcher.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.launcher.state_path.write_text(json.dumps(state), encoding="utf-8")
        health = {
            "service": SERVICE_NAME,
            "mode": SERVICE_MODE,
            "instance_id": state["instance_id"],
            "sync": {"running": False},
        }
        with (
            patch.object(self.launcher, "_health", return_value=health),
            patch.object(self.launcher, "_open_browser", return_value=True),
        ):
            result = self.launcher.start()
        self.assertTrue(result.ok)
        self.assertTrue(result.already_running)
        self.assertFalse(result.attention)

    def test_stop_refuses_stale_state_for_another_instance(self) -> None:
        state = self.valid_state()
        self.launcher.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.launcher.state_path.write_text(json.dumps(state), encoding="utf-8")
        health = {
            "service": SERVICE_NAME,
            "mode": SERVICE_MODE,
            "instance_id": str(uuid.uuid4()),
            "sync": {"running": False},
        }
        with patch.object(self.launcher, "_health", return_value=health):
            result = self.launcher.stop()
        self.assertFalse(result.ok)
        self.assertIn("another launcher instance", result.message)
        self.assertFalse(self.launcher._stop_path(str(state["shutdown_token"])).exists())

    def test_stop_refuses_unmanaged_instance(self) -> None:
        health = {"service": SERVICE_NAME, "mode": SERVICE_MODE, "sync": {"running": False}}
        with patch.object(self.launcher, "_health", return_value=health):
            result = self.launcher.stop()
        self.assertFalse(result.ok)
        self.assertIn("not started by this background launcher", result.message)

    def test_stop_refuses_while_sync_is_running(self) -> None:
        state = self.valid_state()
        self.launcher.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.launcher.state_path.write_text(json.dumps(state), encoding="utf-8")
        health = {
            "service": SERVICE_NAME,
            "mode": SERVICE_MODE,
            "instance_id": state["instance_id"],
            "sync": {"running": True},
        }
        with patch.object(self.launcher, "_health", return_value=health):
            result = self.launcher.stop()
        self.assertFalse(result.ok)
        self.assertIn("sync is still running", result.message)
        self.assertFalse(self.launcher._stop_path(str(state["shutdown_token"])).exists())

    def test_stop_retains_state_for_matching_process_before_health_is_ready(self) -> None:
        state = self.valid_state()
        state["status"] = "starting"
        self.launcher.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.launcher.state_path.write_text(json.dumps(state), encoding="utf-8")
        with (
            patch.object(self.launcher, "_health", return_value=None),
            patch.object(self.launcher, "_port_is_open", return_value=False),
            patch.object(self.launcher, "_state_process_status", return_value="match"),
        ):
            result = self.launcher.stop()
        self.assertFalse(result.ok)
        self.assertTrue(self.launcher.state_path.exists())
        stop_request = self.launcher._stop_path(str(state["shutdown_token"]))
        self.assertTrue(stop_request.exists())
        payload = json.loads(stop_request.read_text(encoding="utf-8"))
        self.assertIs(payload["force"], False)


class DesktopLauncherLifecycleTests(unittest.TestCase):
    def test_start_duplicate_and_graceful_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            launcher = ShortTrackerLauncher(
                PROJECT_ROOT,
                data_dir=temporary,
                port=available_port(),
                start_timeout=12,
                stop_timeout=8,
            )
            try:
                started = launcher.start(open_browser=False, skip_startup_sync=True)
                self.assertTrue(started.ok, started.message)
                state = json.loads(launcher.state_path.read_text(encoding="utf-8"))
                first_pid = state["pid"]

                duplicate = launcher.start(open_browser=False, skip_startup_sync=True)
                self.assertTrue(duplicate.ok, duplicate.message)
                self.assertTrue(duplicate.already_running)
                second_state = json.loads(launcher.state_path.read_text(encoding="utf-8"))
                self.assertEqual(first_pid, second_state["pid"])

                stopped = launcher.stop()
                self.assertTrue(stopped.ok, stopped.message)
                self.assertFalse(launcher.state_path.exists())
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and launcher._port_is_open():
                    time.sleep(0.05)
                self.assertFalse(launcher._port_is_open())
            finally:
                if launcher._health() is not None:
                    launcher.stop(force_during_sync=True)

    def test_two_data_directories_cannot_claim_the_same_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            port = available_port()
            launchers = [
                ShortTrackerLauncher(
                    PROJECT_ROOT,
                    data_dir=root / "first",
                    port=port,
                    start_timeout=12,
                    stop_timeout=8,
                ),
                ShortTrackerLauncher(
                    PROJECT_ROOT,
                    data_dir=root / "second",
                    port=port,
                    start_timeout=12,
                    stop_timeout=8,
                ),
            ]
            results = [None, None]

            def start(index: int) -> None:
                results[index] = launchers[index].start(
                    open_browser=False,
                    skip_startup_sync=True,
                )

            threads = [threading.Thread(target=start, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            self.assertTrue(all(not thread.is_alive() for thread in threads))

            state_owners = [launcher for launcher in launchers if launcher.state_path.exists()]
            self.assertEqual(len(state_owners), 1, results)
            owner = state_owners[0]
            owner_state = json.loads(owner.state_path.read_text(encoding="utf-8"))
            health = owner._health(expected_instance_id=owner_state["instance_id"])
            self.assertIsNotNone(health)
            non_owner_result = results[1] if owner is launchers[0] else results[0]
            self.assertTrue(
                not non_owner_result.ok or non_owner_result.attention,
                non_owner_result.message,
            )
            try:
                stopped = owner.stop()
                self.assertTrue(stopped.ok, stopped.message)
            finally:
                if owner._health() is not None:
                    owner.stop(force_during_sync=True)


if __name__ == "__main__":
    unittest.main()
