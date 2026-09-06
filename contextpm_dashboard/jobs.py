# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
Background job execution and in-memory log capture for the dashboard.

The pipeline runs in a worker thread so that the browser can keep polling
``/api/logs`` and ``/api/status`` while the process maps are being discovered.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple


class LogBuffer(logging.Handler):
    """A logging handler that keeps the most recent records in memory."""

    def __init__(self, maxlen: int = 5000) -> None:
        super().__init__(level=logging.DEBUG)
        self._records: deque = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - logging API
        if record.name.startswith("werkzeug"):
            return  # HTTP access log lines are not interesting in the dashboard
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            message = str(record.getMessage())
        with self._lock:
            self._seq += 1
            self._records.append(
                {
                    "seq": self._seq,
                    "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                }
            )

    def since(self, seq: int, limit: int = 2000) -> Tuple[List[Dict[str, Any]], int]:
        """Return the records newer than ``seq`` and the latest sequence number."""
        with self._lock:
            items = [r for r in self._records if r["seq"] > seq]
            return items[-limit:], self._seq

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


def install_log_buffer(maxlen: int = 5000) -> LogBuffer:
    """Attach a LogBuffer to the root logger (once) and return it."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, LogBuffer):
            return handler
    buffer = LogBuffer(maxlen=maxlen)
    root.addHandler(buffer)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    logging.captureWarnings(True)
    return buffer


class JobManager:
    """Runs one job at a time in a background thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._counter = 0
        self.current: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []
        self.log = logging.getLogger("contextpm.jobs")

    def is_running(self) -> bool:
        with self._lock:
            return self.current is not None and self.current["status"] == "running"

    def start(self, name: str, target: Callable[[], Any]) -> Dict[str, Any]:
        """Start ``target`` in a thread; raises RuntimeError if a job is running."""
        with self._lock:
            if self.current is not None and self.current["status"] == "running":
                raise RuntimeError(f"A job is already running: {self.current['name']}")
            self._counter += 1
            job = {
                "id": self._counter,
                "name": name,
                "status": "running",
                "started_at": dt.datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "duration_seconds": None,
                "error": None,
                "result": None,
            }
            self.current = job

        def runner() -> None:
            started = time.time()
            self.log.info("=" * 70)
            self.log.info("Job #%s started: %s", job["id"], name)
            try:
                job["result"] = target()
                job["status"] = "done"
                self.log.info("Job #%s finished successfully in %.1f s", job["id"], time.time() - started)
            except Exception as exc:  # noqa: BLE001 - report every failure to the UI
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                self.log.exception("Job #%s failed: %s", job["id"], exc)
            finally:
                job["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
                job["duration_seconds"] = round(time.time() - started, 1)
                with self._lock:
                    self.history.append(dict(job))
                    self.history = self.history[-20:]

        thread = threading.Thread(target=runner, name=f"contextpm-job-{job['id']}", daemon=True)
        self._thread = thread
        thread.start()
        return job

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self.current is not None and self.current["status"] == "running",
                "current": dict(self.current) if self.current else None,
                "history": [dict(h) for h in self.history[-5:]],
            }
