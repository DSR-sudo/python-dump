"""Mirror console output into one timestamped local session log."""

from __future__ import annotations

import datetime
import sys
import threading
from pathlib import Path
from typing import TextIO


class _TeeStream:
    def __init__(self, original: TextIO, session_log: "SessionLog") -> None:
        self._original = original
        self._session_log = session_log

    @property
    def encoding(self) -> str:
        return self._original.encoding or "utf-8"

    def write(self, text: str) -> int:
        written = self._original.write(text)
        self._session_log.write(text)
        return written

    def flush(self) -> None:
        self._original.flush()
        self._session_log.flush()

    def isatty(self) -> bool:
        return self._original.isatty()


class SessionLog:
    def __init__(self, path: Path, stream: TextIO) -> None:
        self.path = path
        self._stream = stream
        self._lock = threading.Lock()
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._stdout_tee = _TeeStream(self._stdout, self)
        self._stderr_tee = _TeeStream(self._stderr, self)

    @classmethod
    def start(cls, root_dir: Path) -> "SessionLog":
        log_dir = root_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = log_dir / f"python_dump_{stamp}.log"
        stream = path.open("x", encoding="utf-8", buffering=1)
        session_log = cls(path, stream)
        sys.stdout = session_log._stdout_tee
        sys.stderr = session_log._stderr_tee
        return session_log

    def write(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._stream.write(text)

    def write_received_log(self, message: str) -> None:
        if not message:
            return
        self.write(f"[LOG] {message}\n")

    def write_console_only(self, text: str) -> None:
        if text:
            self._stdout.write(text)
            self._stdout.flush()

    def flush(self) -> None:
        with self._lock:
            self._stream.flush()

    def close(self) -> None:
        if sys.stdout is self._stdout_tee:
            sys.stdout = self._stdout
        if sys.stderr is self._stderr_tee:
            sys.stderr = self._stderr
        with self._lock:
            self._stream.flush()
            self._stream.close()
