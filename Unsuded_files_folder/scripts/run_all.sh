#!/bin/bash
# ======================================================================
# AWCP temp3 — ONE-SHOT runner (registry + token monitoring & control).
#
# Brings up, in order:
#   1. venv + dependencies                  (first run only; includes tiktoken for
#                                            the pre-execution budget pre-check)
#   2. Docker telemetry stack               (OTel Collector / Tempo / Prometheus
#                                            / Loki / Grafana — starts the Docker
#                                            daemon itself if it isn't running)
#   3. Temporal dev server                  (engine :7233, UI :8233) with
#                                            NAMESPACED task queues, so this
#                                            radar never collides with another
#                                            radar sharing the same Temporal
#   4. MCP control server                   (:8002, SSE)
#   5. Agent service                        (:8001, FastAPI direct REST path —
#                                            auto-registers agents with the radar)
#   6. Temporal governance worker           (awcp-governance-queue — drives
#                                            AgentGovernanceWorkflow over MCP)
#   7. Control surface                      (:8003, browser UI → Temporal)
#   8. a DEMO seeder (background)           registers a demo agent and drives the
#                                            full token-control loop so the UIs
#                                            show results immediately (DEMO=1 to enable)
#   9. the Agent Radar (foreground, :8090)  registry + governance + awcp.laminar
#                                            + pre-execution budget pre-check gate
#
# Usage:   bash scripts/run_all.sh                 Ctrl+C stops radar/Temporal/MCP
# Env:     SKIP_TELEMETRY=1   don't start the Docker stack
#          SKIP_MCP=1         don't start the MCP server
#          SKIP_INSTALL=1     skip pip install on an existing venv
#          DEMO=0             don't seed the demo agent / token events
#          RADAR_PORT=8090    radar port
#          LMNR_PROJECT_API_KEY=...   also dual-export spans to Laminar
#          LMNR_* / AGENT_RADAR_* / OTEL_*  all pass straight through
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
export PYTHONPATH="$ROOT/src"
# Load .env if present — set LMNR_PROJECT_API_KEY, LMNR_* overrides, etc.
[ -f .env ] && set -a && source .env && set +a
export OTEL_ENABLED="${OTEL_ENABLED:-true}"
RADAR_PORT="${RADAR_PORT:-8090}"
LOGDIR="${TMPDIR:-/tmp}/awcp-temp3-run"; mkdir -p "$LOGDIR"
TEMPORAL_PID=""; MCP_PID=""; AGENT_SVC_PID=""; WORKER_PID=""; CTRL_SVC_PID=""

# Namespaced Temporal queues (overridable): two radars on one Temporal dev
# server must NOT share queue names, or one worker steals (and fails) the
# other's workflows. These defaults keep temp3 isolated.
export AGENT_RADAR_TASK_QUEUE="${AGENT_RADAR_TASK_QUEUE:-temp3-radar-onboarding}"
export AGENT_EXEC_TASK_QUEUE="${AGENT_EXEC_TASK_QUEUE:-temp3-task-execution}"

# Point awcp.laminar's OTLP exporter at the self-hosted Laminar stack (started
# by the Docker compose below). Override to keep using Laminar cloud instead:
#   LMNR_OTLP_ENDPOINT=https://api.lmnr.ai:8443 bash scripts/run_all.sh
export LMNR_OTLP_ENDPOINT="${LMNR_OTLP_ENDPOINT:-http://localhost:8881}"
export LMNR_OTLP_PROTOCOL="${LMNR_OTLP_PROTOCOL:-grpc}"

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

# Return the working directory of the listener on a given port (macOS lsof).
_port_pid(){  lsof -ti :"$1" 2>/dev/null | head -1; }
_pid_cwd(){   lsof -p "$1" 2>/dev/null | awk '/cwd/{print $NF}' | head -1; }

# Kill any process on PORT whose cwd is NOT this ROOT.
# No-op if the port is free or already owned by us.
ensure_ours(){
  local port=$1
  local pid; pid=$(_port_pid "$port") || true
  [ -z "$pid" ] && return 0
  local cwd; cwd=$(_pid_cwd "$pid")
  [ "$cwd" = "$ROOT" ] && return 0
  warn "Port :$port occupied by foreign process PID=$pid (${cwd:-unknown}) — evicting…"
  kill -9 "$pid" 2>/dev/null || true
  sleep 0.5
}

# ── pre-flight: evict any stale processes from other directories ──────────────
# Temporal (:7233/:8233) is shared — never evict it.
# Docker ports are owned by Docker Desktop — never evict them.
for _p in 8090 8001 8002 8003; do ensure_ours "$_p"; done
unset _p

cleanup(){
  echo
  say "Shutting down…"
  [ -n "$CTRL_SVC_PID" ]  && kill "$CTRL_SVC_PID"  2>/dev/null || true
  [ -n "$WORKER_PID" ]    && kill "$WORKER_PID"    2>/dev/null || true
  [ -n "$AGENT_SVC_PID" ] && kill "$AGENT_SVC_PID" 2>/dev/null || true
  [ -n "$MCP_PID" ]       && kill "$MCP_PID"       2>/dev/null || true
  [ -n "$TEMPORAL_PID" ]  && kill "$TEMPORAL_PID"  2>/dev/null || true
  say "Stopped all services started by this script."
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
      warn "docker compose failed — radar still runs; OTel exports will warn until it's up."
  else
    warn "Docker daemon didn't come up — skipping telemetry stack."
  fi
fi

# ── 3. Temporal dev server (must be up BEFORE the radar) ──────────────
if [ -n "$(port_open 7233)" ]; then
  say "Temporal already running on :7233 — reusing it (queues: $AGENT_RADAR_TASK_QUEUE / $AGENT_EXEC_TASK_QUEUE)."
elif command -v temporal >/dev/null 2>&1; then
  say "Starting Temporal dev server (engine :7233, UI :8233)…"
  nohup temporal server start-dev --ip 127.0.0.1 > "$LOGDIR/temporal.log" 2>&1 &
  TEMPORAL_PID=$!
  for i in $(seq 1 30); do [ -n "$(port_open 7233)" ] && break; sleep 1; done
  [ -n "$(port_open 7233)" ] && say "Temporal is up." || warn "Temporal didn't come up — radar will onboard inline."
else
  warn "Temporal CLI not found — radar will onboard inline (install: brew install temporal)."
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

# ── 5. Agent service (:8001, FastAPI direct REST path) — background ──────
if [ -n "$(port_open 8001)" ]; then
  say "Agent service already on :8001 — reusing it."
else
  say "Starting agent service on :8001…"
  nohup ./.venv/bin/uvicorn awcp.service:app --host 0.0.0.0 --port 8001 \
    > "$LOGDIR/agent_svc.log" 2>&1 &
  AGENT_SVC_PID=$!
fi

# ── 6. Temporal governance worker (awcp-governance-queue) — background ───
# Drives AgentGovernanceWorkflow / DynamicAskWorkflow over the MCP server.
# Only started when Temporal is confirmed up — the worker exits immediately
# if it can't connect, so skipping avoids a spurious background process.
if [ -n "$(port_open 7233)" ]; then
  say "Starting Temporal governance worker (awcp-governance-queue)…"
  nohup ./.venv/bin/python -m awcp.temporal.worker.run_worker \
    > "$LOGDIR/worker.log" 2>&1 &
  WORKER_PID=$!
else
  warn "Temporal not up — skipping governance worker (start Temporal, then re-run)."
fi

# ── 7. Control surface (:8003, browser UI → Temporal) — background ───────
# Provides the live step-by-step view and prompt submission UI.
# Requires Temporal to trigger workflows; skipped if Temporal isn't up.
if [ -n "$(port_open 8003)" ]; then
  say "Control surface already on :8003 — reusing it."
elif [ -n "$(port_open 7233)" ]; then
  say "Starting control surface on :8003…"
  nohup ./.venv/bin/uvicorn awcp.control.api:app --host 0.0.0.0 --port 8003 \
    > "$LOGDIR/control.log" 2>&1 &
  CTRL_SVC_PID=$!
else
  warn "Temporal not up — skipping control surface (:8003 needs Temporal to trigger workflows)."
fi

# ── 8. OPTIONAL canned demonstration (off by default — DEMO=1 to enable) ─
# The DEFAULT experience is REAL: agents auto-report per-LLM-call token usage
# to the radar and are metered against the budget (LMNR_TOKEN_BUDGET / risk-tier).
# DEMO=1 runs a scripted walk-through (allow → warn → exhausted → gate denies)
# using a synthetic agent with a small demo budget so the loop completes fast.
if [ "${DEMO:-0}" = "1" ]; then
  (
    B="http://localhost:${RADAR_PORT}"
    for i in $(seq 1 60); do curl -sf "$B/healthz" >/dev/null 2>&1 && break; sleep 1; done
    curl -sf "$B/healthz" >/dev/null 2>&1 || exit 0
    sleep 1
    # Use /agents/announce so the demo agent is onboarded instantly via Temporal
    # (same as a real awcp_kit agent would do) rather than waiting for the scanner.
    curl -s -X POST "$B/agents/announce" -H 'content-type: application/json' -d '{
      "name":"demo-writer","kind":"agent_framework","framework":"langgraph","risk":"high",
      "telemetry_enabled":true,"feature_flags":{"governed_writes":true},
      "policy_callbacks":["'"$B"'/agents/reg-demo-writer/gate"]}' >/dev/null
    sleep 5                                   # let onboarding admit it
    # small DEMO budget so the synthetic burn exhausts it quickly (demo-only knob)
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

# ── 9. the Agent Radar (foreground) ───────────────────────────────────
echo
echo "  ── AWCP temp3 — full stack (agent-announce onboarding) ────────────────────────────────────"
echo "     Registry / radar  : http://localhost:${RADAR_PORT}"
echo "     Announce endpoint : http://localhost:${RADAR_PORT}/agents/announce  (instant Temporal onboarding)"
echo "     Token monitor     : http://localhost:${RADAR_PORT}/laminar/ui"
echo "     LLM gateway       : http://localhost:${RADAR_PORT}/llm   (pre-execution budget gate)"
echo "     Agent service     : http://localhost:8001  (direct REST + /docs)"
echo "     MCP server        : http://localhost:8002  (SSE)"
echo "     Control surface   : http://localhost:8003  (browser UI → Temporal)"
echo "     Temporal UI       : http://localhost:8233  (queues: $AGENT_RADAR_TASK_QUEUE, $AGENT_EXEC_TASK_QUEUE)"
echo "     Grafana           : http://localhost:3000  (admin / awcp1234)"
echo "     Prometheus        : http://localhost:9090"
echo "     Laminar UI        : http://localhost:5667  (self-hosted LLM observability)"
echo "       OTLP endpoint   : ${LMNR_OTLP_ENDPOINT}  (gRPC → lmnr-app-server)"
[ -n "${LMNR_PROJECT_API_KEY:-}" ] && \
echo "       Laminar export  : ON  (spans streaming to local Laminar)" || \
echo "       Laminar export  : OFF — visit http://localhost:5667, create a project,"
[ -n "${LMNR_PROJECT_API_KEY:-}" ] || \
echo "                         copy the API key, then re-run with LMNR_PROJECT_API_KEY=<key>"
[ "${DEMO:-0}" = "1" ] && \
echo "     Demo (DEMO=1)     : synthetic 'demo-writer' walks the control loop (~20s after boot)."
echo
echo "  Logs (for background services):"
echo "    tail -f ${LOGDIR}/agent_svc.log   # agent service (:8001)"
echo "    tail -f ${LOGDIR}/worker.log       # temporal governance worker"
echo "    tail -f ${LOGDIR}/control.log      # control surface (:8003)"
echo "    tail -f ${LOGDIR}/mcp.log          # MCP server (:8002)"
echo
echo "  ▶ Point agents at the LLM gateway (AWCP_GATEWAY_UPSTREAM / OLLAMA_BASE)"
echo "    to activate the pre-execution budget pre-check across all runtimes."
echo "  ▶ Press Ctrl+C to stop all services started by this script."
echo "  ──────────────────────────────────────────────────────────────"
echo
./.venv/bin/uvicorn awcp.radar.api:app --host 0.0.0.0 --port "${RADAR_PORT}"
