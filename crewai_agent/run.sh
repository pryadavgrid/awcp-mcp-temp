#!/bin/bash
# Launch the CrewAI runtime agent.
# Launched with an ABSOLUTE script path so a process-scanning registry
# (agent_radar) can read this file and detect the `crewai` import.
set -e
cd "$(dirname "$0")"

# auto-setup: create venv + install requirements on first run
if [ ! -x ".venv/bin/python" ]; then
  echo "📦 First run — creating venv + installing requirements…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

LOG="${TMPDIR:-/tmp}/crewai-agent.log"
echo "👥 Starting CrewAI runtime agent (background) on http://localhost:${CREW_PORT:-8101}"
echo "   (free / local Ollama model: ${CREW_MODEL:-ollama/llama3.1:8b})"
nohup ./.venv/bin/python "$PWD/agent_runtime.py" > "$LOG" 2>&1 &
echo "✅ running — PID $!   logs: $LOG"
echo "   stop: pkill -f '$PWD/agent_runtime.py'"
