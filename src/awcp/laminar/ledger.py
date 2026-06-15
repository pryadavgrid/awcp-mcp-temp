"""Token ledger — the MONITORING half: who spent what, when, on which model.

A thread-safe, per-agent ledger of LLM token usage with two horizons:

  * a SLIDING WINDOW (LMNR_BUDGET_WINDOW_S, default 1 h) — what budget.py
    evaluates budgets against, so control reacts to *recent* burn rate and an
    idle agent naturally recovers as old records age out of the window;
  * LIFETIME totals — for the dashboard and capacity planning.

Each record is one LLM call: {ts, agent_id, task_id, step type, model,
input/output tokens, cost}. Cost is computed from the env-driven price table
(model-name LONGEST-PREFIX match, $ per 1M tokens). With the default empty
table cost is 0.0 — which is the honest number for local Ollama models.

Evidence: when LMNR_LEDGER_PATH is set, every record is also appended as one
JSON line — the durable trail the magazine's Evidence Ledger calls for. (The
real Evidence Ledger is a later component; this JSONL is its seam.)
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque

from awcp.laminar import config


def price_for(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost in dollars via longest-prefix match against LMNR_PRICE_TABLE.
    Prefix matching means one entry like "gpt-4o" covers "gpt-4o-2024-11-20"
    without enumerating every dated variant — no hardcoded model list."""
    best = ""
    for prefix in config.PRICE_TABLE:
        if model.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    if not best:
        return 0.0
    p = config.PRICE_TABLE[best]
    return (input_tokens * float(p.get("input", 0.0))
            + output_tokens * float(p.get("output", 0.0))) / 1_000_000.0


class TokenLedger:
    """In-memory token accounting. One instance per radar process (see LEDGER)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # agent_id -> ring of recent records (window pruning walks this)
        self._records: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=config.RECORDS_MAX))
        # agent_id -> lifetime totals (never pruned)
        self._lifetime: dict[str, dict] = defaultdict(
            lambda: {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0})

    # ── write path ────────────────────────────────────────────────────────────

    def record(self, *, agent_id: str, task_id: str, step: str, model: str,
               input_tokens: int, output_tokens: int) -> dict:
        """Append one LLM call's usage; returns the stored record (with cost)."""
        rec = {
            "ts": time.time(),
            "agent_id": agent_id,
            "task_id": task_id,
            "step": step,                      # event type as REPORTED (no fixed taxonomy)
            "model": model or "unknown",
            "input_tokens": max(0, int(input_tokens)),
            "output_tokens": max(0, int(output_tokens)),
        }
        rec["cost"] = price_for(rec["model"], rec["input_tokens"], rec["output_tokens"])
        with self._lock:
            self._records[agent_id].append(rec)
            lt = self._lifetime[agent_id]
            lt["input_tokens"] += rec["input_tokens"]
            lt["output_tokens"] += rec["output_tokens"]
            lt["cost"] += rec["cost"]
            lt["calls"] += 1
        self._append_evidence(rec)
        return rec

    def _append_evidence(self, rec: dict) -> None:
        """Durable JSONL trail — only when the operator opted in via env."""
        if not config.LEDGER_PATH:
            return
        try:
            with open(config.LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:           # noqa: BLE001 — evidence must not break accounting
            pass

    # ── read path ─────────────────────────────────────────────────────────────

    def window_usage(self, agent_id: str, window_s: float | None = None) -> dict:
        """Token totals inside the sliding window (the number budgets gate on)."""
        horizon = time.time() - (window_s or config.BUDGET_WINDOW_S)
        used_in = used_out = calls = 0
        cost = 0.0
        last_model = ""
        with self._lock:
            for rec in self._records.get(agent_id, ()):
                if rec["ts"] >= horizon:
                    used_in += rec["input_tokens"]
                    used_out += rec["output_tokens"]
                    cost += rec["cost"]
                    calls += 1
                    last_model = rec["model"]
        return {"input_tokens": used_in, "output_tokens": used_out,
                "total_tokens": used_in + used_out, "cost": round(cost, 6),
                "calls": calls, "last_model": last_model}

    def lifetime_usage(self, agent_id: str) -> dict:
        with self._lock:
            lt = dict(self._lifetime.get(
                agent_id,
                {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0}))
        lt["total_tokens"] = lt["input_tokens"] + lt["output_tokens"]
        lt["cost"] = round(lt["cost"], 6)
        return lt

    def recent(self, agent_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._records.get(agent_id, ()))[-limit:][::-1]

    def agents(self) -> list[str]:
        with self._lock:
            return sorted(self._lifetime.keys())

    def reset_window(self, agent_id: str) -> int:
        """Operator action: clear the agent's recent records so its window usage
        returns to zero (used together with restoring autonomy after a token
        breach). Lifetime totals are preserved — evidence is never erased."""
        with self._lock:
            n = len(self._records.get(agent_id, ()))
            self._records.pop(agent_id, None)
        return n


# Module-level singleton, same pattern as radar/telemetry.py's metrics.
LEDGER = TokenLedger()
