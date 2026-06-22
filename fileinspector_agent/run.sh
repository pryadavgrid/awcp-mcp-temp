#!/bin/bash
# Launch the File Inspector agent (absolute script path so agent_radar can read
# this file and detect the `langgraph` import).
set -e
cd "$(dirname "$0")"
export AGENT_RADAR_URL="${AGENT_RADAR_URL:-http://localhost:8000}"  # register with the gateway-mounted radar

# auto-setup: create venv + install requirements on first run
if [ ! -x ".venv/bin/python" ]; then
  echo "📦 First run — creating venv + installing requirements…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

LOG="${TMPDIR:-/tmp}/fileinspector-agent.log"
echo "🗂  Starting File Inspector agent (background) on http://localhost:${FILE_PORT:-8104}"
echo "   (free / local Ollama · text=${FILE_MODEL:-qwen2.5:7b} · vision=${VISION_MODEL:-qwen2.5vl:7b})"
nohup ./.venv/bin/python "$PWD/fileinspector_agent.py" > "$LOG" 2>&1 &
echo "✅ running — PID $!   logs: $LOG"
echo "   stop: pkill -f '$PWD/fileinspector_agent.py'"
