#!/bin/bash
# ======================================================================
# AWCP temp2 — ONE-SHOT runner (registry + token monitoring & control).
#
# Brings up, in order:
#   1. venv + dependencies                  (first run only)
#   2. Docker telemetry stack               (OTel Collector / Tempo / Prometheus
#                                            / Loki / Grafana — starts the Docker
#                                            daemon itself if it isn't running)
#   3. Temporal dev server                  (engine :7233, UI :8233) with
#                                            NAMESPACED task queues, so this
#                                            radar never collides with another
#                                            radar sharing the same Temporal
#   4. MCP control server                   (:8002, SSE — the radar's onboarding
#                                            link_mcp step can enumerate it)
#   5. a DEMO seeder (background)           registers a demo agent and drives the
#                                            full token-control loop so the UIs
#                                            show results immediately (DEMO=0 to skip)
#   6. the Agent Radar (foreground, :8090)  registry + governance + awcp.laminar
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
export OTEL_ENABLED="${OTEL_ENABLED:-true}"
RADAR_PORT="${RADAR_PORT:-8090}"
LOGDIR="${TMPDIR:-/tmp}/awcp-temp2-run"; mkdir -p "$LOGDIR"
TEMPORAL_PID=""; MCP_PID=""

# Namespaced Temporal queues (overridable): two radars on one Temporal dev
# server must NOT share queue names, or one worker steals (and fails) the
# other's workflows. These defaults keep temp2 isolated.
export AGENT_RADAR_TASK_QUEUE="${AGENT_RADAR_TASK_QUEUE:-temp2-radar-onboarding}"
export AGENT_EXEC_TASK_QUEUE="${AGENT_EXEC_TASK_QUEUE:-temp2-task-execution}"

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
  say "Stopped the radar (+ Temporal/MCP if this script started them)."
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

# ── 5. OPTIONAL canned demonstration (OFF by default: DEMO=1 to enable) ──
# The DEFAULT, non-hardcoded experience is REAL: start your agents via
# agents/*/run.sh — each auto-reports its REAL per-LLM-call token usage to this
# radar (AWCP_RADAR_URL) and is metered against the DEFAULT token budget
# (LMNR_TOKEN_BUDGET / risk-tiered). Nothing is faked there.
# This block only runs when DEMO=1: a scripted walk-through of the control loop
# (allow -> warn -> exhausted -> degraded -> gate denies) using a synthetic
# "demo-writer" with an intentionally small demo budget so the loop completes fast.
if [ "${DEMO:-0}" = "1" ]; then
  (
    B="http://localhost:${RADAR_PORT}"
    for i in $(seq 1 60); do curl -sf "$B/healthz" >/dev/null 2>&1 && break; sleep 1; done
    curl -sf "$B/healthz" >/dev/null 2>&1 || exit 0
    sleep 1
    curl -s -X POST "$B/agents/register" -H 'content-type: application/json' -d '{
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

# ── 6. the Agent Radar (foreground) ───────────────────────────────────
echo
echo "  ── AWCP temp2 is up (registry + token monitoring & control) ──"
echo "     Registry (radar)  : http://localhost:${RADAR_PORT}        ('tokens ↗' chip + Tokens column)"
echo "     Token monitor     : http://localhost:${RADAR_PORT}/laminar/ui"
echo "     Temporal UI       : http://localhost:8233   (queues: $AGENT_RADAR_TASK_QUEUE, $AGENT_EXEC_TASK_QUEUE)"
echo "     Grafana           : http://localhost:3000   (admin / awcp1234)"
echo "     Prometheus        : http://localhost:9090"
echo "     MCP server        : http://localhost:8002   (SSE)"
[ "${DEMO:-0}" = "1" ] && \
echo "     Demo (DEMO=1)     : synthetic 'demo-writer' walks the control loop (~20s after boot)."
[ -n "${LMNR_PROJECT_API_KEY:-}" ] && \
echo "     Laminar export    : ON → ${LMNR_OTLP_ENDPOINT:-https://api.lmnr.ai:8443}" || \
echo "     Laminar export    : off (set LMNR_PROJECT_API_KEY to dual-export spans)"
echo
echo "  ▶ Start your agents (agents/*/run.sh): each auto-reports REAL token usage here"
echo "    and is metered against the DEFAULT budget (LMNR_TOKEN_BUDGET / risk-tiered)."
echo "    To keep an agent fully decoupled instead: AWCP_RADAR_URL= bash <agent>/run.sh"
echo "  ▶ Press Ctrl+C to stop."
echo "  ──────────────────────────────────────────────────────────────"
echo
./.venv/bin/uvicorn awcp.radar.api:app --host 0.0.0.0 --port "${RADAR_PORT}"
