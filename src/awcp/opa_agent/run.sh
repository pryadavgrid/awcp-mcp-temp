#!/bin/bash
# Launch the OPA Agent — the hidden, SLM-reasoned tool-call policy decision point.
#
# It now lives INSIDE this repo (src/awcp/opa_agent) but still runs as its OWN
# background process on its OWN port, because it is the single cross-process tier
# authority and must stay invisible to the control plane: it does NOT export
# AGENT_RADAR_URL (so it never self-registers), and the scanner is told to skip it
# via AGENT_RADAR_EXCLUDE (set on the GATEWAY side — see scripts/run_everything.sh).
#
# It reaches: the local model runtime (Ollama) so a SMALL model reasons each tool's
# risk tier; the gateway (optional Laminar logging); and, optionally, an OPA server
# for the Rego block decision. Everything is env-driven.
set -e
cd "$(dirname "$0")"
ROOT="$(cd ../../.. && pwd)"          # repo root (src/awcp/opa_agent -> ../../..)

export OPA_PORT="${OPA_PORT:-8105}"
export AWCP_GATEWAY_URL="${AWCP_GATEWAY_URL:-http://localhost:8000}"   # for /laminar/record
# The SLM that reasons the tier. Defaults resolve a local Ollama runtime + a small
# model; override OPA_SLM_BASE / OPA_SLM_MODEL to point elsewhere.
export OPA_SLM_BASE="${OPA_SLM_BASE:-${AWCP_GATEWAY_UPSTREAM:-${OLLAMA_BASE:-http://localhost:11434}}}"
export OPA_SLM_MODEL="${OPA_SLM_MODEL:-gemma2:2b}"
# AWCP_OPA_URL is inherited if set (e.g. http://localhost:8181); empty ⇒ Python fallback.

# Prefer the repo's venv (fastapi/uvicorn/httpx already installed for the gateway);
# fall back to a local venv if the repo one is absent.
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  if [ ! -x ".venv/bin/python" ]; then
    echo "📦 First run — creating venv + installing requirements…"
    python3 -m venv .venv
    ./.venv/bin/pip install --quiet --upgrade pip
    ./.venv/bin/pip install --quiet -r requirements.txt
  fi
  PY="$PWD/.venv/bin/python"
fi

LOG="${TMPDIR:-/tmp}/opa-agent.log"
echo "🛡️  Starting OPA Agent (SLM tool-call PDP, hidden) on http://localhost:${OPA_PORT}"
echo "   SLM: ${OPA_SLM_MODEL} @ ${OPA_SLM_BASE}   OPA Rego: ${AWCP_OPA_URL:-<none — deterministic fallback>}   gateway: ${AWCP_GATEWAY_URL}"
nohup "$PY" "$PWD/opa_agent.py" > "$LOG" 2>&1 &
echo "✅ running — PID $!   logs: $LOG"
echo "   stop: pkill -f '$PWD/opa_agent.py'"
