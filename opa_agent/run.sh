#!/bin/bash
# Launch the OPA Agent — the hidden tool-call policy decision point.
#
# Unlike the worker agents, this one is DELIBERATELY invisible to the control
# plane: it does NOT export AGENT_RADAR_URL (so it never self-registers), and the
# control-plane scanner is told to skip it via AGENT_RADAR_EXCLUDE (set on the
# GATEWAY side — see scripts/run_everything.sh, which appends this script's name).
#
# It only needs to reach the gateway (for Laminar tool-token logging) and,
# optionally, an OPA server (for the Rego decision). Everything is env-driven.
set -e
cd "$(dirname "$0")"

export OPA_PORT="${OPA_PORT:-8105}"
export AWCP_GATEWAY_URL="${AWCP_GATEWAY_URL:-http://localhost:8000}"   # for /laminar/record
# AWCP_OPA_URL is inherited if set (e.g. http://localhost:8181); empty ⇒ Python fallback.

# auto-setup: create venv + install requirements on first run
if [ ! -x ".venv/bin/python" ]; then
  echo "📦 First run — creating venv + installing requirements…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

LOG="${TMPDIR:-/tmp}/opa-agent.log"
echo "🛡️  Starting OPA Agent (background, hidden from control plane) on http://localhost:${OPA_PORT}"
echo "   OPA Rego: ${AWCP_OPA_URL:-<none — deterministic fallback>}   gateway: ${AWCP_GATEWAY_URL}"
nohup ./.venv/bin/python "$PWD/opa_agent.py" > "$LOG" 2>&1 &
echo "✅ running — PID $!   logs: $LOG"
echo "   stop: pkill -f '$PWD/opa_agent.py'"
