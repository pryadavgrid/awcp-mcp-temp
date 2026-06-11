"""Background scanner: periodically detects running agents and updates the store."""

from __future__ import annotations

import os
import threading

from awcp.radar.detectors import scan_all
from awcp.radar.store import REGISTRY

SCAN_INTERVAL = float(os.getenv("AGENT_RADAR_SCAN_INTERVAL", "5"))


class Scanner:
    def __init__(self, interval: float = SCAN_INTERVAL) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                REGISTRY.reconcile_scan(scan_all())
            except Exception:
                pass
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="agent-radar-scanner", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


SCANNER = Scanner()
