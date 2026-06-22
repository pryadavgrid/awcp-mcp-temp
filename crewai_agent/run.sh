#!/bin/bash
# Launch the CrewAI runtime agent.
# Launched with an ABSOLUTE script path so a process-scanning registry
# (agent_radar) can read this file and detect the `crewai` import.
set -e
cd "$(dirname "$0")"
export AGENT_RADAR_URL="${AGENT_RADAR_URL:-http://localhost:8000}"  # register with the gateway-mounted radar

# auto-setup: create venv on first run (crewai requires Python <3.14), always sync requirements.
# Pick the first available interpreter <3.14 instead of hardcoding one version, so the
# agent isn't tied to a specific python (e.g. 3.12) being installed.
if [ ! -x ".venv/bin/python" ]; then
  PY=""
  for c in python3.12 python3.13 python3.11 python3.10; do
    command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
  done
  if [ -z "$PY" ]; then
    echo "❌ CrewAI needs Python <3.14 (3.10–3.13). Install one, e.g. 'brew install python@3.12'." >&2
    exit 1
  fi
  echo "📦 First run — creating venv with $PY ($($PY --version 2>&1))…"
  "$PY" -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
fi
./.venv/bin/pip install --quiet -r requirements.txt

LOG="${TMPDIR:-/tmp}/crewai-agent.log"
echo "👥 Starting CrewAI runtime agent (background) on http://localhost:${CREW_PORT:-8101}"
echo "   (free / local Ollama model: ${CREW_MODEL:-ollama/llama3.1:8b})"
nohup ./.venv/bin/python "$PWD/crewai_agent.py" > "$LOG" 2>&1 &
echo "✅ running — PID $!   logs: $LOG"
echo "   stop: pkill -f '$PWD/crewai_agent.py'"
