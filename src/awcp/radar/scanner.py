"""Background scanner: periodically detects running agents and updates the store."""

from __future__ import annotations

import os
import threading
import time

from awcp.radar.detectors import scan_all
from awcp.radar.store import REGISTRY
from awcp.radar.telemetry import get_radar_metrics, radar_span, log

SCAN_INTERVAL = float(os.getenv("AGENT_RADAR_SCAN_INTERVAL", "30"))
# Passive AgentCard discovery on scan-sourced entries (additive enrichment). Each
# entry is attempted at most once (the `not entry.card` guard), with a short
# timeout so it can't stall the scan cycle. Env-gated off-switch; SSRF-guarded, so
# in production (AGENT_RADAR_ALLOW_LOOPBACK=false) only routable endpoints are tried.
SCAN_CARD_DISCOVERY = os.getenv("AGENT_RADAR_SCAN_CARD_DISCOVERY", "true").lower() == "true"
SCAN_CARD_TIMEOUT = float(os.getenv("AGENT_RADAR_SCAN_CARD_TIMEOUT", "3"))


def _try_fetch_card_sync(agent_id: str, endpoint: str) -> None:
    """Best-effort SYNCHRONOUS card fetch for a scan-sourced entry (the scanner runs
    in a plain thread with no event loop, so this stays sync). Only called when the
    entry has no card yet. Any failure — unsafe URL, no card, timeout — is silent."""
    import time
    import httpx
    from awcp.radar.netguard import assert_safe_url, UnsafeURLError
    from awcp.radar.card import skill_ids
    card_url = endpoint.rstrip("/") + "/.well-known/agent.json"
    try:
        assert_safe_url(card_url)
        resp = httpx.get(card_url, timeout=SCAN_CARD_TIMEOUT,
                         headers={"Accept": "application/json",
                                  "ngrok-skip-browser-warning": "true"})
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, dict):
            return
        skills = skill_ids(raw)
        REGISTRY.patch(agent_id, card=raw, skills=skills,
                       card_url=card_url, card_fetched_at=time.time())
        log.info("radar.scan.card agent_id=%s skills=%d", agent_id, len(skills))
    except UnsafeURLError:
        pass
    except Exception:  # noqa: BLE001 — passive discovery is best-effort, never noisy
        pass


def _discover_cards() -> None:
    """After a scan, fetch a card for any alive scan-sourced entry that exposes an
    http(s) endpoint and has no card yet. Each entry is attempted at most once."""
    if not SCAN_CARD_DISCOVERY:
        return
    for entry in REGISTRY.all():
        if (entry.source == "scan" and entry.alive and not entry.card
                and (entry.endpoint or "").startswith(("http://", "https://"))):
            _try_fetch_card_sync(entry.id, entry.endpoint)


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

                    # Passive AgentCard discovery for newly/again-seen scan entries
                    # (additive; guarded + best-effort; skips entries already carded).
                    _discover_cards()

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
