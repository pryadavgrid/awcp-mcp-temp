"""Configuration for awcp.laminar — every knob is ENVIRONMENT-DRIVEN.

Nothing in this module (or this package) hardcodes an endpoint, a budget, a
price, or an agent/model name. Defaults below are deploy-time fallbacks only,
following the exact pattern the radar already uses in radar/policy.py
(AGENT_RADAR_LADDER / AGENT_RADAR_RISK_BUDGET / ...).

Environment variables
=====================
LMNR_ENABLED                "true"   master switch for this whole package
LMNR_PROJECT_API_KEY        ""       Laminar project key. EMPTY => the Laminar
                                     dual-export is skipped, but LOCAL token
                                     monitoring + budget control still run.
LMNR_OTLP_ENDPOINT          "https://api.lmnr.ai:8443"
                                     Laminar's OTLP ingest (point at a
                                     self-hosted Laminar to keep data on-prem).
LMNR_OTLP_PROTOCOL          "grpc"   "grpc" | "http/protobuf" for the Laminar
                                     exporter (HTTP is the robust fallback —
                                     gRPC can stall under some ASGI servers).
LMNR_TOKEN_BUDGET           "50000"  default tokens-per-window per agent
LMNR_RISK_TOKEN_BUDGET      "low:100000,medium:50000,high:20000"
                                     risk-tier -> budget map (same format as
                                     AGENT_RADAR_RISK_BUDGET). Higher risk =
                                     smaller spend allowed before control acts.
LMNR_BUDGET_WINDOW_S        "3600"   sliding-window length in seconds
LMNR_WARN_RATIO             "0.8"    fraction of budget that triggers a WARN
LMNR_PRICE_TABLE            "{}"     JSON: model-name-prefix -> {"input": $ per
                                     1M input tokens, "output": $ per 1M output
                                     tokens}. Empty => cost 0.0 (honest for
                                     local Ollama models, which are free).
                                     Example:
                                       {"gpt-4o": {"input":2.5,"output":10},
                                        "llama3.1": {"input":0,"output":0}}
LMNR_LEDGER_PATH            ""       optional JSONL evidence file. EMPTY =>
                                     no file is written (in-memory only).
LMNR_RECORDS_MAX            "500"    per-agent in-memory record ring size
LMNR_TRACE_URL_TEMPLATE     ""       optional deep-link template for a token
                                     record's OTel trace, with a "{trace_id}"
                                     placeholder. EMPTY => the API/UI still show
                                     the raw trace id, just not as a link (so no
                                     broken links by default). Example (Grafana
                                     Explore → Tempo):
                                       http://localhost:3000/explore?...{trace_id}...
"""

from __future__ import annotations

import json
import os


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def _env_int(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


def _env_float(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _parse_risk_budget() -> dict[str, int]:
    """Risk tier -> token budget, "low:100000,medium:50000,high:20000" format.
    Mirrors radar/policy.py's _parse_risk_budget so operators configure both
    failure budgets and token budgets the same way. Unknown tiers fall back to
    LMNR_TOKEN_BUDGET at lookup time (budget.py)."""
    out: dict[str, int] = {}
    raw = os.getenv("LMNR_RISK_TOKEN_BUDGET", "low:100000,medium:50000,high:20000")
    for pair in raw.split(","):
        if ":" in pair:
            name, _, val = pair.partition(":")
            try:
                out[name.strip().lower()] = int(val)
            except ValueError:
                pass
    return out


def _parse_price_table() -> dict[str, dict[str, float]]:
    """LMNR_PRICE_TABLE may be inline JSON or a path to a JSON file — so a
    deployment can manage prices as a mounted config file without code change."""
    raw = os.getenv("LMNR_PRICE_TABLE", "").strip()
    if not raw:
        return {}
    try:
        if raw.startswith("{"):
            return json.loads(raw)
        with open(raw, encoding="utf-8") as f:          # treat as a file path
            return json.load(f)
    except Exception:                                    # malformed => no prices
        return {}


# ── resolved settings (read once at import; restart to re-read, like policy.py) ──
ENABLED: bool = _env_bool("LMNR_ENABLED", "true")
PROJECT_API_KEY: str = os.getenv("LMNR_PROJECT_API_KEY", "")
OTLP_ENDPOINT: str = os.getenv("LMNR_OTLP_ENDPOINT", "https://api.lmnr.ai:8443")
OTLP_PROTOCOL: str = os.getenv("LMNR_OTLP_PROTOCOL", "grpc").lower()

DEFAULT_TOKEN_BUDGET: int = _env_int("LMNR_TOKEN_BUDGET", "50000")
RISK_TOKEN_BUDGET: dict[str, int] = _parse_risk_budget()
BUDGET_WINDOW_S: float = _env_float("LMNR_BUDGET_WINDOW_S", "3600")
WARN_RATIO: float = _env_float("LMNR_WARN_RATIO", "0.8")

PRICE_TABLE: dict[str, dict[str, float]] = _parse_price_table()
LEDGER_PATH: str = os.getenv("LMNR_LEDGER_PATH", "")
RECORDS_MAX: int = _env_int("LMNR_RECORDS_MAX", "500")
TRACE_URL_TEMPLATE: str = os.getenv("LMNR_TRACE_URL_TEMPLATE", "")


def trace_url(trace_id: str | None) -> str | None:
    """Build a deep-link to a trace from TRACE_URL_TEMPLATE, or None if the
    template is unset / the id is missing (so the UI shows the id as plain text
    rather than a broken link)."""
    if not (TRACE_URL_TEMPLATE and trace_id):
        return None
    return TRACE_URL_TEMPLATE.replace("{trace_id}", trace_id)
