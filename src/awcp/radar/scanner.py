"""Background scanner: periodically detects running agents and updates the store."""

from __future__ import annotations

import os
import threading
import time

from awcp.radar.detectors import scan_all
from awcp.radar.store import REGISTRY
from awcp.radar.telemetry import get_radar_metrics, radar_span, log

SCAN_INTERVAL = float(os.getenv("AGENT_RADAR_SCAN_INTERVAL", "30"))


class Scanner:
    def __init__(self, interval: float = SCAN_INTERVAL) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        metrics = get_radar_metrics()
        while not self._stop.is_set():
            t_start = time.monotonic()
            found = 0
            new = 0
            error = False
            try:
                with radar_span("radar.scan.cycle", {"interval_s": self.interval}) as span:
                    # Detect: enumerate all running agent processes
                    detected = list(scan_all())
                    found = len(detected)
                    span.set_attribute("agents.detected", found)

                    # Reconcile: merge into registry and count net-new entries
                    pre = len(REGISTRY.all())
                    REGISTRY.reconcile_scan(detected)
                    post = len(REGISTRY.all())
                    new = max(0, post - pre)

                    span.set_attribute("agents.new", new)
                    span.set_attribute("agents.total", post)
                    log.info(
                        "radar.scan agents_found=%d new=%d total=%d dur_ms=%.1f",
                        found, new, post, (time.monotonic() - t_start) * 1000,
                    )
            except Exception as exc:
                error = True
                log.warning("radar.scan.error error=%r", exc, exc_info=True)

            metrics.record_scan(
                duration=time.monotonic() - t_start,
                found=found,
                new=new,
                error=error,
            )
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
