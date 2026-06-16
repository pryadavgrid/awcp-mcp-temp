#!/bin/bash
# ======================================================================
# AWCP — ONE PORT, EVERYTHING runner.
#
# Brings the WHOLE platform up behind a SINGLE port (:8000, the gateway), so
# you open ONE base URL and reach all three surfaces:
#
#     /              registry dashboard (agents, autonomy, gate, Tokens column)
#     /user/*        user API  — list agents, POST /user/ask to run one
#     /agents,/tasks,/events,/healthz   registry / radar API
#     /laminar/ui    token monitor — per-agent token bar (used / remaining)
#     /docs          OpenAPI for all of the above
#
# It also starts everything the gateway needs for full Temporal + OTel
# visibility, in order:
#   1. venv + dependencies                  (first run only)
#   2. Docker telemetry stack               (OTel Collector / Tempo / Prometheus
#                                            / Loki / Grafana — starts Docker itself
#                                            if the daemon isn't running)
#   3. Temporal dev server                  (engine :7233, UI :8233)
#   4. MCP control server                   (:8002, SSE)
#   5. a DEMO seeder (background, DEMO=1)    registers a demo agent + drives the
#                                            token-control loop so the UIs show
#                                            data immediately
#   6. the AWCP Gateway (foreground, :8000)  registry + token monitor + /user API
#
# Usage:   bash scripts/run_everything.sh            Ctrl+C stops gateway/Temporal/MCP
# Env:     SKIP_TELEMETRY=1   don't start the Docker stack
#          SKIP_MCP=1         don't start the MCP server
#          SKIP_INSTALL=1     skip pip install on an existing venv
#          DEMO=1             seed a synthetic agent + token-control walkthrough
#          GATEWAY_PORT=8000  gateway port
#          AWCP_AGENTS_DIR=…  external agent bundle (default: Downloads bundle)
#          LMNR_PROJECT_API_KEY=…   also dual-export spans to Laminar
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
export PYTHONPATH="$ROOT/src"
export OTEL_ENABLED="${OTEL_ENABLED:-true}"
GATEWAY_PORT="${GATEWAY_PORT:-8000}"
LOGDIR="${TMPDIR:-/tmp}/awcp-everything-run"; mkdir -p "$LOGDIR"
TEMPORAL_PID=""; MCP_PID=""

# The external agent bundle the gateway runs via /user/ask. Agents launched from
# here are told to report to THIS gateway (root), so the
# agent -> radar -> Temporal/OTel pipeline is wired end to end.
export AWCP_AGENTS_DIR="${AWCP_AGENTS_DIR:-/Users/moshaik/Desktop/Projects/AWCP_Demo/awcp-mcp-temp-DS_Prateek}"
export AWCP_AGENT_RADAR_URL="${AWCP_AGENT_RADAR_URL:-http://localhost:${GATEWAY_PORT}}"

# Temporal task queues the gateway's in-process workers listen on. Kept distinct
# from run_all.sh's temp2-* queues so the gateway and a standalone radar can share
# one Temporal dev server without stealing each other's workflows.
export AGENT_RADAR_TASK_QUEUE="${AGENT_RADAR_TASK_QUEUE:-agent-radar-onboarding}"
export AGENT_EXEC_TASK_QUEUE="${AGENT_EXEC_TASK_QUEUE:-agent-task-execution}"

say(){  printf "\033[1;36m▶ %s\033[0m\n" "$*"; }
warn(){ printf "\033[1;33m! %s\033[0m\n" "$*"; }

port_open(){ ./.venv/bin/python - "$1" 2>/dev/null <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1]))); print("open")
except Exception:
    pass
PY
}

cleanup(){
  echo
  say "Shutting down…"
  [ -n "$MCP_PID" ]      && kill "$MCP_PID"      2>/dev/null || true
  [ -n "$TEMPORAL_PID" ] && kill "$TEMPORAL_PID" 2>/dev/null || true
  say "Stopped the gateway (+ Temporal/MCP if this script started them)."
  echo "  Telemetry stack left running — stop it with:"
  echo "    docker compose -f observability/docker-compose.yml down"
}
trap cleanup EXIT INT TERM

# ── 1. venv + dependencies ────────────────────────────────────────────
if [ ! -x ".venv/bin/python" ]; then
  say "Creating virtualenv + installing requirements (first run only)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
elif [ "${SKIP_INSTALL:-0}" != "1" ]; then
  say "venv present — ensuring requirements are installed…"
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

# ── 2. telemetry stack (start the Docker daemon if needed, then the stack) ──
if [ "${SKIP_TELEMETRY:-0}" = "1" ]; then
  warn "SKIP_TELEMETRY=1 — not starting the telemetry stack."
elif ! command -v docker >/dev/null 2>&1; then
  warn "Docker not installed — skipping telemetry stack (OTel exports will warn; harmless)."
else
  if ! docker info >/dev/null 2>&1; then
    say "Docker daemon not running — starting Docker…"
    if [ "$(uname)" = "Darwin" ]; then
      open -a Docker 2>/dev/null || open -a "Docker Desktop" 2>/dev/null || true
    elif command -v systemctl >/dev/null 2>&1; then
      sudo systemctl start docker 2>/dev/null || true
    fi
    printf "  waiting for Docker daemon"
    for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; printf "."; sleep 2; done
    echo
  fi
  if docker info >/dev/null 2>&1; then
    say "Starting telemetry stack (OTel/Tempo/Prometheus/Loki/Grafana)…"
    docker compose -f observability/docker-compose.yml up -d || \
      warn "docker compose failed — gateway still runs; OTel exports will warn until it's up."
  else
    warn "Docker daemon didn't come up — skipping telemetry stack."
  fi
fi

# ── 3. Temporal dev server (must be up BEFORE the gateway) ────────────
if [ -n "$(port_open 7233)" ]; then
  say "Temporal already running on :7233 — reusing it (queues: $AGENT_RADAR_TASK_QUEUE / $AGENT_EXEC_TASK_QUEUE)."
elif command -v temporal >/dev/null 2>&1; then
  say "Starting Temporal dev server (engine :7233, UI :8233)…"
  nohup temporal server start-dev --ip 127.0.0.1 > "$LOGDIR/temporal.log" 2>&1 &
  TEMPORAL_PID=$!
  for i in $(seq 1 30); do [ -n "$(port_open 7233)" ] && break; sleep 1; done
  [ -n "$(port_open 7233)" ] && say "Temporal is up." || warn "Temporal didn't come up — gateway will onboard inline."
else
  warn "Temporal CLI not found — gateway will onboard inline (install: brew install temporal)."
fi

# ── 4. MCP control server (:8002, SSE) — background ───────────────────
if [ "${SKIP_MCP:-0}" = "1" ]; then
  warn "SKIP_MCP=1 — not starting the MCP server."
elif [ -n "$(port_open 8002)" ]; then
  say "MCP server already on :8002 — reusing it."
else
  say "Starting MCP control server on :8002 (SSE)…"
  nohup ./.venv/bin/uvicorn awcp.mcp.server:app --host 0.0.0.0 --port 8002 \
    > "$LOGDIR/mcp.log" 2>&1 &
  MCP_PID=$!
fi

# ── 5. OPTIONAL canned demonstration (DEMO=1) ─────────────────────────
# Posts straight to the gateway ROOT paths (/agents, /laminar, /tasks) — the same
# URLs the real bundle agents use — so the registry + token bar light up at once.
if [ "${DEMO:-0}" = "1" ]; then
  (
    B="http://localhost:${GATEWAY_PORT}"
    for i in $(seq 1 60); do curl -sf "$B/healthz" >/dev/null 2>&1 && break; sleep 1; done
    curl -sf "$B/healthz" >/dev/null 2>&1 || exit 0
    sleep 1
    curl -s -X POST "$B/agents/register" -H 'content-type: application/json' -d '{
      "name":"demo-writer","kind":"agent_framework","framework":"langgraph","risk":"high",
      "telemetry_enabled":true,"feature_flags":{"governed_writes":true},
      "policy_callbacks":["'"$B"'/agents/reg-demo-writer/gate"]}' >/dev/null
    sleep 5                                   # let onboarding admit it
    curl -s -X POST "$B/laminar/budgets/reg-demo-writer" \
      -H 'content-type: application/json' -d '{"tokens":2000}' >/dev/null
    curl -s -X POST "$B/tasks/execution/start" -H 'content-type: application/json' \
      -d '{"agent_id":"reg-demo-writer","task_id":"demo-1","goal":"demo: burn the token budget","framework":"langgraph"}' >/dev/null
    for n in 1 2 3 4 5; do
      curl -s -X POST "$B/tasks/execution/demo-1/event" -H 'content-type: application/json' \
        -d '{"type":"llm_called","model":"llama3.1:8b","call_n":'"$n"',"extra":{"input_tokens":600,"output_tokens":100}}' >/dev/null
      sleep 1
    done
    curl -s -X POST "$B/tasks/execution/demo-1/complete" -H 'content-type: application/json' \
      -d '{"status":"done","result":"demo finished"}' >/dev/null
    # prove the control loop: this gate call lands in "Recent decisions" as DENY
    curl -s -X POST "$B/agents/reg-demo-writer/gate" -H 'content-type: application/json' \
      -d '{"action":"external_post","write":true}' >/dev/null
  ) > "$LOGDIR/demo.log" 2>&1 &
fi

# ── 6. the AWCP Gateway (foreground) ──────────────────────────────────
echo
echo "  ── AWCP is up on ONE port (:${GATEWAY_PORT}) ─────────────────────────────"
echo "     Everything            : http://localhost:${GATEWAY_PORT}"
echo "     Registry dashboard    : http://localhost:${GATEWAY_PORT}/            (Tokens column)"
echo "     Token monitor         : http://localhost:${GATEWAY_PORT}/laminar/ui  (used / remaining bar)"
echo "     User API              : http://localhost:${GATEWAY_PORT}/user/agents · POST /user/ask"
echo "     API docs (all groups) : http://localhost:${GATEWAY_PORT}/docs"
echo "     Temporal UI           : http://localhost:8233   (queues: $AGENT_RADAR_TASK_QUEUE, $AGENT_EXEC_TASK_QUEUE)"
echo "     Grafana               : http://localhost:3000   (admin / awcp1234)"
echo "     Prometheus            : http://localhost:9090"
echo "     MCP server            : http://localhost:8002   (SSE)"
echo "     agents bundle         : $AWCP_AGENTS_DIR"
[ "${DEMO:-0}" = "1" ] && \
echo "     Demo (DEMO=1)         : synthetic 'demo-writer' walks the token-control loop (~20s after boot)."
[ -n "${LMNR_PROJECT_API_KEY:-}" ] && \
echo "     Laminar export        : ON → ${LMNR_OTLP_ENDPOINT:-https://api.lmnr.ai:8443}" || \
echo "     Laminar export        : off (set LMNR_PROJECT_API_KEY to dual-export spans)"
echo
echo "  ▶ Run a REAL agent (real LLM/tool/web calls → Temporal activities + OTel):"
echo "      curl -s localhost:${GATEWAY_PORT}/user/agents"
echo "      curl -s -X POST localhost:${GATEWAY_PORT}/user/ask -H 'content-type: application/json' \\"
echo "           -d '{\"agent\":\"langgraph_agent\",\"input\":\"summarize the latest arxiv paper on agents\"}'"
echo "  ▶ Press Ctrl+C to stop."
echo "  ──────────────────────────────────────────────────────────────"
echo
./.venv/bin/uvicorn awcp.gateway.app:app --host 0.0.0.0 --port "${GATEWAY_PORT}"
