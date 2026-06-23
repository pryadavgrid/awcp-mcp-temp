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


def _default_http_endpoint() -> str:
    """Derive the HTTP OTLP endpoint from the gRPC endpoint when not set.
    Self-hosted docker-compose maps gRPC→:8881 and HTTP→:8880 (port-1 offset).
    Cloud uses separate fixed ports (:8443 gRPC, :443 HTTP)."""
    explicit = os.getenv("LMNR_OTLP_HTTP_ENDPOINT", "").strip()
    if explicit:
        return explicit
    grpc = OTLP_ENDPOINT  # already resolved above
    # Cloud endpoints — canonical HTTP base is api.lmnr.ai (port 443 implicit)
    if "api.lmnr.ai" in grpc:
        return "https://api.lmnr.ai"
    # Self-hosted: gRPC port is HTTP port + 1 per the lmnr docker-compose convention
    try:
        from urllib.parse import urlparse
        p = urlparse(grpc)
        if p.port:
            return f"{p.scheme}://{p.hostname}:{p.port - 1}"
    except Exception:
        pass
    return grpc  # last resort: same base, caller appends /v1/traces


# HTTP OTLP endpoint — used both by the manual OTLP fallback and the per-request
# Laminar exporter fan-out. Defaults to the gRPC endpoint with port-1 for
# self-hosted (see _default_http_endpoint), or api.lmnr.ai for cloud.
OTLP_HTTP_ENDPOINT: str = _default_http_endpoint()

DEFAULT_TOKEN_BUDGET: int = _env_int("LMNR_TOKEN_BUDGET", "50000")
RISK_TOKEN_BUDGET: dict[str, int] = _parse_risk_budget()
BUDGET_WINDOW_S: float = _env_float("LMNR_BUDGET_WINDOW_S", "3600")
WARN_RATIO: float = _env_float("LMNR_WARN_RATIO", "0.8")
# Tolerated overshoot above the budget before an agent counts as "exhausted".
# 0.10 = a 10% grace band, so control acts at 110% of budget rather than exactly
# 100% — applied identically at the pre-check (projection) and the reactive
# (post-call) evaluation. Set 0 for a hard 100% limit.
OVERSHOOT_RATIO: float = _env_float("LMNR_OVERSHOOT_RATIO", "0.10")
# When true, on_breach fires at the warn threshold (WARN_RATIO * budget) in
# addition to exhausted.  This steps the autonomy ladder down one rung earlier
# so enforcement fires before the hard limit, reducing overshoot for agents
# that report tokens via execution events rather than the LLM gateway.
ENFORCE_AT_WARN: bool = _env_bool("LMNR_ENFORCE_AT_WARN", "true")

# When true (default), only LLM/token + AWCP governance spans (plus the
# task-lifecycle request spans named in EXPORT_KEEP_SPAN_NAMES) are fanned out to
# Laminar. Laminar is an LLM-observability backend, so the high-frequency HTTP
# poll spans (the UIs hit /agents, /laminar/usage, /events every couple seconds)
# are kept OUT of it and remain in Tempo/Grafana only. Without this the handful
# of `laminar.token.usage` LLM spans get buried under THOUSANDS of GET /agents
# polling spans in the Laminar dashboard, so token/trace data looks "missing".
# Set LMNR_EXPORT_ONLY_LLM=false to dual-export every span to Laminar as before.
EXPORT_ONLY_LLM: bool = _env_bool("LMNR_EXPORT_ONLY_LLM", "true")

# Span-name substrings ALWAYS kept in the Laminar fan-out even while
# EXPORT_ONLY_LLM filters out the polling noise. The `laminar.token.usage` span
# is created as a CHILD of the task-execution request span (POST
# /tasks/execution/{task_id}/event); if that parent is dropped, the token span
# reaches Laminar parentless and renders as an orphaned `laminar.token.usage`
# row with no trace tree — instead of nesting inside its full
# `POST /tasks/execution/...` trace with token counts rolled up to the root.
# Keeping these spans (and their `http receive`/`http send` ASGI children, which
# share the substring) restores that tree WITHOUT re-admitting the GET /agents
# flood. Comma-separated; set LMNR_EXPORT_KEEP_SPANS="" to keep only LLM spans.
EXPORT_KEEP_SPAN_NAMES: list[str] = [
    s.strip() for s in os.getenv("LMNR_EXPORT_KEEP_SPANS", "tasks/execution").split(",")
    if s.strip()
]

# Agent ids to HIDE from the Token Monitor feed (GET /laminar/usage). Hidden infra
# like the OPA agent self-registers on the radar (so it shows there) but should NOT
# appear in the token monitor — it spends no metered tokens. Comma-separated, exact
# agent ids; defaults to the OPA agent's radar id (kept in sync via run_everything).
# Set LMNR_USAGE_EXCLUDE="" to show everything.
USAGE_EXCLUDE: set[str] = {
    s.strip() for s in os.getenv("LMNR_USAGE_EXCLUDE", "agent-opa").split(",")
    if s.strip()
}

PRICE_TABLE: dict[str, dict[str, float]] = _parse_price_table()
LEDGER_PATH: str = os.getenv("LMNR_LEDGER_PATH", "/tmp/awcp-token-ledger.jsonl")
RECORDS_MAX: int = _env_int("LMNR_RECORDS_MAX", "500")
# Separate high-capacity accounting deque (window budget scans this, not RECORDS_MAX).
# Much larger so fast agents (>500 calls/hour) are never under-counted.
ACCT_MAX: int = _env_int("LMNR_ACCT_MAX", "10000")
# Default output-token buffer added to every pre-check estimate when the request
# does not declare max_tokens / num_predict.  0 = only use explicit caps.
OUTPUT_BUFFER: int = _env_int("LMNR_OUTPUT_BUFFER", "0")
TRACE_URL_TEMPLATE: str = os.getenv("LMNR_TRACE_URL_TEMPLATE", "")


def trace_url(trace_id: str | None) -> str | None:
    """Build a deep-link to a trace from TRACE_URL_TEMPLATE, or None if the
    template is unset / the id is missing (so the UI shows the id as plain text
    rather than a broken link)."""
    if not (TRACE_URL_TEMPLATE and trace_id):
        return None
    return TRACE_URL_TEMPLATE.replace("{trace_id}", trace_id)
