"""Small cross-process file lock used to serialize FCA synchronisation."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import threading
from typing import BinaryIO


class ProcessFileLock:
    """Hold one byte in a file until explicitly released.

    The lock is advisory on POSIX and mandatory for cooperating processes on
    Windows.  Short Tracker uses one well-known path per data directory, so a
    CLI sync and multiple local server instances cannot update the same FCA
    archive/database concurrently.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._guard = threading.Lock()
        self._handle: BinaryIO | None = None

    @property
    def held(self) -> bool:
        with self._guard:
            return self._handle is not None

    def try_acquire(self) -> bool:
        with self._guard:
            if self._handle is not None:
                raise RuntimeError("process file lock is already held by this object")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            return False
                        raise
                else:
                    import fcntl

                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        return False
                self._handle = handle
                handle = None
                return True
            finally:
                if handle is not None:
                    handle.close()

    def release(self) -> None:
        with self._guard:
            handle = self._handle
            if handle is None:
                return
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
                self._handle = None


__all__ = ["ProcessFileLock"]
