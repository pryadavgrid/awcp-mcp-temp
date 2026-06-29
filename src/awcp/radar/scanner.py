"""Background scanner: periodically detects running agents and updates the store."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from awcp.radar.detectors import scan_all
from awcp.radar.models import AgentEntry
from awcp.radar.store import REGISTRY
from awcp.radar.telemetry import get_radar_metrics, radar_span, log

SCAN_INTERVAL = float(os.getenv("AGENT_RADAR_SCAN_INTERVAL", "30"))
# Run the OS process enumeration in a SUBPROCESS with a hard timeout so a wedged
# macOS process table (psutil.process_iter stuck in a syscall that never releases
# the GIL) can be killed instead of freezing the gateway — keeping uvicorn able to
# bind :8000 no matter what the OS is doing. Off-switch falls back to in-process.
SCAN_SUBPROCESS = os.getenv("AGENT_RADAR_SCAN_SUBPROCESS", "true").lower() == "true"
SCAN_TIMEOUT = float(os.getenv("AGENT_RADAR_SCAN_TIMEOUT", "20"))
# .../src/awcp/radar/scanner.py -> .../src  (so the child can import awcp)
_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def scan_all_safe() -> list[AgentEntry]:
    """Enumerate running agent processes WITHOUT risk of freezing this process.

    Delegates the psutil scan to ``python -m awcp.radar.detectors`` and waits on it
    with a hard timeout. If the OS process table wedges, the child is killed and we
    return [] (skip this cycle, try again next interval) — the gateway stays up and
    serves the dashboard. Set AGENT_RADAR_SCAN_SUBPROCESS=false to scan in-process.
    """
    if not SCAN_SUBPROCESS:
        return list(scan_all())
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "awcp.radar.detectors"],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT, env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning("radar.scan.subprocess timed out after %.0fs — OS process table "
                    "may be wedged; skipping cycle (gateway stays up)", SCAN_TIMEOUT)
        return []
    except Exception as exc:  # noqa: BLE001 — a scan hiccup must never crash the loop
        log.warning("radar.scan.subprocess failed error=%r — skipping cycle", exc)
        return []
    out = (proc.stdout or "").strip()
    if not out:
        return []
    try:
        return [AgentEntry.model_validate(d) for d in json.loads(out)]
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.scan.subprocess parse failed error=%r — skipping cycle", exc)
        return []
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
                    # Detect: enumerate all running agent processes (in a subprocess
                    # with a timeout, so a wedged OS scan can't freeze the gateway).
                    detected = scan_all_safe()
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
