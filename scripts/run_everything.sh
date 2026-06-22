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
# It also starts everything you need for full Temporal + OTel visibility AND the
# React UI, in order:
#   1. venv + dependencies                  (first run only)
#   2. Docker telemetry stack               (OTel Collector / Tempo / Prometheus
#                                            / Loki / Grafana — starts Docker itself
#                                            if the daemon isn't running)
#   3. Temporal dev server                  (engine :7233, UI :8233)
#   4. Ollama model runtime                  (:11434 — the LLM the agents call)
#   5. MCP control server                   (:8002, SSE)
#   6. a DEMO seeder (background, DEMO=1)    registers a demo agent + drives the
#                                            token-control loop so the UIs show
#                                            data immediately
#   7. the React UI (background, :5173)      Vite dev server (the dashboard you
#                                            give prompts from)
#   8. the AWCP Gateway (foreground, :8000)  registry + token monitor + /user API
#
# Open http://localhost:5173 — pick an agent, type a goal, watch the live step
# timeline. Ctrl+C stops the gateway + everything this script started.
#
# Usage:   bash scripts/run_everything.sh
# Env:     SKIP_TELEMETRY=1   don't start the Docker stack
#          SKIP_MCP=1         don't start the MCP server
#          SKIP_OLLAMA=1      don't start Ollama
#          SKIP_UI=1          don't start the React UI (Vite)
#          SKIP_INSTALL=1     skip pip install on an existing venv
#          DEMO=1             seed a synthetic agent + token-control walkthrough
#          GATEWAY_PORT=8000  gateway port
#          --- toggleable-guard demo (defaults below; see the block further down) ---
#          AGENT_RADAR_REQUIRE_OBSERVED_TELEMETRY=true   restore strict onboarding
#          AGENT_RADAR_REQUIRE_OBSERVED_POLICY=true       (agents quarantined until
#                                                          telemetry+policy observed)
#          AGENT_RADAR_RISK_BUDGET=low:5,medium:3,high:1  restore tight failure budgets
#          LMNR_RISK_TOKEN_BUDGET=low:100000,medium:50000,high:20000  tight token budgets
#          UI_PORT=5173       React UI (Vite) port
#          AWCP_AGENTS_DIR=…  external agent bundle (default: Downloads bundle)
#          LMNR_PROJECT_API_KEY=…   also dual-export spans to Laminar
#          LMNR_OTLP_ENDPOINT=…     Laminar OTLP ingest (default: self-hosted
#                                   http://localhost:8881; use
#                                   https://api.lmnr.ai:8443 for Laminar Cloud)
# ======================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

# Load .env (if present) so secrets like LMNR_PROJECT_API_KEY are picked up
# without exporting them by hand. Lines are KEY=VALUE; blank lines, comments and
# an optional leading `export ` are tolerated, surrounding quotes stripped. A
# value already in the environment wins, so `LMNR_PROJECT_API_KEY=… bash …`
# still overrides the file.
if [ -f "$ROOT/.env" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#export }"
    case "$line" in ''|\#*) continue ;; esac
    key="${line%%=*}"; val="${line#*=}"
    case "$line" in *=*) ;; *) continue ;; esac     # skip lines without '='
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    case "$key" in ''|*[!A-Za-z0-9_]*) continue ;; esac
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ -z "${!key:-}" ] && export "$key=$val"
  done < "$ROOT/.env"
fi

export PYTHONPATH="$ROOT/src"
export OTEL_ENABLED="${OTEL_ENABLED:-true}"

# Laminar OTLP ingest. Default to the SELF-HOSTED lmnr docker stack (the
# lmnr-app-server container maps gRPC→:8881 and HTTP→:8880; dashboard at
# http://localhost:5667) so spans + token usage land on the LOCAL Laminar
# rather than the cloud. The exporter derives the HTTP endpoint (:8880) from
# this automatically. Override for Laminar Cloud:
#   LMNR_OTLP_ENDPOINT=https://api.lmnr.ai:8443 bash scripts/run_everything.sh
export LMNR_OTLP_ENDPOINT="${LMNR_OTLP_ENDPOINT:-http://localhost:8881}"

GATEWAY_PORT="${GATEWAY_PORT:-8000}"
UI_PORT="${UI_PORT:-5173}"
LOGDIR="${TMPDIR:-/tmp}/awcp-everything-run"; mkdir -p "$LOGDIR"
TEMPORAL_PID=""; MCP_PID=""; OLLAMA_PID=""; UI_PID=""

# The external agent bundle the gateway runs via /user/ask. Agents launched from
# here are told to report to THIS gateway (root), so the
# agent -> radar -> Temporal/OTel pipeline is wired end to end.
export AWCP_AGENTS_DIR="${AWCP_AGENTS_DIR:-/Users/pchandra/CAPSTONE/DEMO1/Agents/awcp-agents}"
export AWCP_AGENT_RADAR_URL="${AWCP_AGENT_RADAR_URL:-http://localhost:${GATEWAY_PORT}}"
# The MCP control server (started in step 5) is the write-action firewall: it
# calls the radar gate at AGENT_RADAR_URL before running a governed tool. The
# radar now lives in THIS gateway, so point it at the gateway port — otherwise
# the MCP server would gate against the old standalone :8090 (nothing there) and
# fail open. Same value as AWCP_AGENT_RADAR_URL; exported under the name the
# server + agent kits actually read. Overridable from the environment.
export AGENT_RADAR_URL="${AGENT_RADAR_URL:-http://localhost:${GATEWAY_PORT}}"
# The agent kits (awcp_kit) read AWCP_RADAR_URL, not AGENT_RADAR_URL, to decide
# whether to self-register + report per-call token usage + call the gate. Without
# it the kit stays fully decoupled (the radar only scan-detects the process, so no
# tokens/budget). Export the SAME gateway-derived value so a bundle agent spawned
# from here reports onto its own row instead of firing at a dead default port.
# All three names resolve from ${GATEWAY_PORT} — no port is hardcoded here.
export AWCP_RADAR_URL="${AWCP_RADAR_URL:-http://localhost:${GATEWAY_PORT}}"

# Route every agent's MODEL calls through the token-aware /llm gateway instead of
# letting them hit Ollama directly — so the tiktoken pre-check can deny an
# over-budget call BEFORE it spends tokens (no bypass). AWCP_GATEWAY_UPSTREAM
# points that proxy at the REAL model runtime so it never loops back on itself;
# OLLAMA_BASE (what agents read) points at the proxy. Both env-overridable.
export AWCP_GATEWAY_UPSTREAM="${AWCP_GATEWAY_UPSTREAM:-http://localhost:11434}"
export OLLAMA_BASE="${OLLAMA_BASE:-http://localhost:${GATEWAY_PORT}/llm}"

# Canonical control-plane DB (registry / governance / evidence / ops). When the
# observability Postgres is up (docker compose, schema from observability/init-db)
# the registry persists to registry.agents instead of the local JSON file, and
# durable governance/evidence flows to the canonical tables. If Postgres is
# unreachable everything transparently falls back to JSON / in-memory.
#
# Least privilege: the app connects as awcp_app (DML; evidence is append-only by
# GRANT — see observability/init-db/01-roles.sql), while a separate ADMIN url (the
# postgres owner) is used ONLY to create monthly partitions at startup. All creds
# default to the init-db values and are env-overridable — nothing host/port is
# hardcoded in the app.
export AGENT_RADAR_DATABASE_URL="${AGENT_RADAR_DATABASE_URL:-postgresql+psycopg://${AWCP_APP_USER:-awcp_app}:${AWCP_APP_PASSWORD:-awcp_app_password}@localhost:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-awcp}}"
export AGENT_RADAR_DB_ADMIN_URL="${AGENT_RADAR_DB_ADMIN_URL:-postgresql+psycopg://${POSTGRES_USER:-awcp}:${POSTGRES_PASSWORD:-awcppassword}@localhost:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-awcp}}"

# Temporal task queues the gateway's in-process workers listen on. Namespaced so
# the gateway and any standalone radar can share one Temporal dev server without
# stealing each other's workflows.
export AGENT_RADAR_TASK_QUEUE="${AGENT_RADAR_TASK_QUEUE:-agent-radar-onboarding}"
export AGENT_EXEC_TASK_QUEUE="${AGENT_EXEC_TASK_QUEUE:-agent-task-execution}"

# ── Toggleable-guard demo defaults ────────────────────────────────────────────
# Make the dashboard's Policy Guard (Agent Hooks → Policy Guard) the SINGLE on/off
# lever for blocking a tool call: deny-list a tool → blocked; remove it → works.
# To do that we relax the OTHER governance layers that would otherwise deny writes
# on a fresh start (quarantine, autonomy degradation, token hard-stop). Every knob
# below is overridable — set them back to restore strict AWCP behaviour.
#
#  1) Trust DECLARED control hooks so bundle agents come up ACTIVE, not quarantined
#     (a quarantined agent has ALL writes blocked, masking the guard). Restore the
#     strict "telemetry/policy observed in execution" onboarding by setting these
#     back to true.
export AGENT_RADAR_REQUIRE_OBSERVED_TELEMETRY="${AGENT_RADAR_REQUIRE_OBSERVED_TELEMETRY:-false}"
export AGENT_RADAR_REQUIRE_OBSERVED_POLICY="${AGENT_RADAR_REQUIRE_OBSERVED_POLICY:-false}"
#  2) Generous FAILURE budget so a guard-blocked task — which the agent reports as
#     a failure — doesn't walk the autonomy ladder down (which would block writes
#     even with the guard off). Default is 3 / per-risk low:5,medium:3,high:1.
export AGENT_RADAR_FAILURE_BUDGET="${AGENT_RADAR_FAILURE_BUDGET:-1000}"
export AGENT_RADAR_RISK_BUDGET="${AGENT_RADAR_RISK_BUDGET:-low:1000,medium:1000,high:1000}"
#  3) Generous TOKEN budget so a few multi-step tasks don't trip the token
#     hard-stop (which also degrades autonomy). To DEMO token control instead,
#     lower a single agent's budget from the Token Monitor UI (per-agent override).
export LMNR_TOKEN_BUDGET="${LMNR_TOKEN_BUDGET:-5000000}"
export LMNR_RISK_TOKEN_BUDGET="${LMNR_RISK_TOKEN_BUDGET:-low:5000000,medium:5000000,high:5000000}"
export LMNR_ENFORCE_AT_WARN="${LMNR_ENFORCE_AT_WARN:-false}"

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
  [ -n "$UI_PID" ]       && kill "$UI_PID"       2>/dev/null || true
  [ -n "$MCP_PID" ]      && kill "$MCP_PID"      2>/dev/null || true
  [ -n "$OLLAMA_PID" ]   && kill "$OLLAMA_PID"   2>/dev/null || true
  [ -n "$TEMPORAL_PID" ] && kill "$TEMPORAL_PID" 2>/dev/null || true
  say "Stopped the gateway (+ Temporal/MCP/Ollama/UI if this script started them)."
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
    say "Starting telemetry stack (OTel/Tempo/Prometheus/Loki/Grafana + Postgres)…"
    docker compose -f observability/docker-compose.yml up -d || \
      warn "docker compose failed — gateway still runs; OTel exports will warn until it's up."
    # Wait for the canonical Postgres to accept connections before the gateway
    # starts, so the registry persists to registry.agents from the first request
    # instead of falling back to JSON. init-db applies the schema on a fresh
    # volume; this also gives it time to finish. Non-fatal: if it never comes up,
    # the radar runs fail-open on JSON/in-memory.
    say "Waiting for Postgres (registry.agents) to be ready…"
    _pg_ok=""
    for i in $(seq 1 30); do
      if docker compose -f observability/docker-compose.yml exec -T postgres \
           pg_isready -U "${POSTGRES_USER:-awcp}" -d "${POSTGRES_DB:-awcp}" >/dev/null 2>&1; then
        _pg_ok=1; break
      fi
      sleep 1
    done
    [ -n "$_pg_ok" ] && say "Postgres is up — registry persists to the canonical schema." \
      || warn "Postgres not ready — registry will run fail-open on JSON until it is."
  else
    warn "Docker daemon didn't come up — skipping telemetry stack."
  fi
fi

# ── 2b. Laminar reachability preflight (the token-monitor dashboard) ──
# The self-hosted Laminar stack (dashboard :5667, OTLP ingest gRPC :8881 /
# HTTP :8880) is NOT started by this script — it runs from its OWN docker-compose
# (the lmnr-ai/lmnr repo), separate from observability/docker-compose.yml above.
# So if a project key is set we verify the ingest is actually reachable here,
# turning a silent "no traces in Laminar" into an explicit, actionable warning.
if [ -n "${LMNR_PROJECT_API_KEY:-}" ]; then
  if [ -n "$(port_open 8881)" ] || [ -n "$(port_open 8880)" ]; then
    say "Laminar ingest reachable (gRPC :8881 / HTTP :8880) — token spans will dual-export to Laminar."
    [ -z "$(port_open 5667)" ] && \
      warn "  …but the Laminar dashboard (:5667) isn't answering — start the lmnr frontend to view the traces."
  else
    warn "LMNR_PROJECT_API_KEY is set but the Laminar stack is NOT running (:8881/:8880/:5667 all closed)."
    warn "  Start it from the lmnr repo first, e.g.:  docker compose -f /path/to/lmnr/docker-compose.yml up -d"
    warn "  Until then token spans have nowhere to land and the :5667 dashboard won't exist."
  fi
else
  warn "LMNR_PROJECT_API_KEY not set — token spans won't reach the :5667 Laminar dashboard"
  warn "  (the LOCAL token monitor at /laminar/ui still works without it)."
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

# Temporal Web UI port differs by who started it: the dev-server serves :8233,
# the docker-compose (auto-setup) UI container maps to :8080. Probe for the real one.
if   [ -n "$(port_open 8233)" ]; then TEMPORAL_UI="http://localhost:8233"
elif [ -n "$(port_open 8080)" ]; then TEMPORAL_UI="http://localhost:8080"
else                                  TEMPORAL_UI="http://localhost:8233"
fi
# Bind every workflow deep-link to the live UI. radar reads AGENT_RADAR_TEMPORAL_UI;
# control + gateway read AWCP_TEMPORAL_UI_BASE; the React UI reads VITE_TEMPORAL_BASE.
# A pre-set env wins (':-') so you can still override.
export AGENT_RADAR_TEMPORAL_UI="${AGENT_RADAR_TEMPORAL_UI:-$TEMPORAL_UI}"
export AWCP_TEMPORAL_UI_BASE="${AWCP_TEMPORAL_UI_BASE:-$TEMPORAL_UI}"

# ── 4. Ollama model runtime (:11434) — the LLM the bundle agents call ──
if [ "${SKIP_OLLAMA:-0}" = "1" ]; then
  warn "SKIP_OLLAMA=1 — not starting Ollama."
elif [ -n "$(port_open 11434)" ]; then
  say "Ollama already running on :11434 — reusing it."
elif command -v ollama >/dev/null 2>&1; then
  say "Starting Ollama (model runtime) on :11434…"
  nohup ollama serve > "$LOGDIR/ollama.log" 2>&1 &
  OLLAMA_PID=$!
  for i in $(seq 1 20); do [ -n "$(port_open 11434)" ] && break; sleep 1; done
  [ -n "$(port_open 11434)" ] && say "Ollama is up." \
    || warn "Ollama didn't come up — agents that need a local model will fail."
else
  warn "Ollama not found — install it and 'ollama pull llama3.1:8b gemma2:2b', else agents fail (https://ollama.com)."
fi

# ── 5. MCP control server (:8002, SSE) — background ───────────────────
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

# ── 6. OPTIONAL canned demonstration (DEMO=1) ─────────────────────────
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

# ── 7. React UI (Vite dev server, :${UI_PORT}) — background ────────────
if [ "${SKIP_UI:-0}" = "1" ]; then
  warn "SKIP_UI=1 — not starting the React UI."
elif [ -n "$(port_open "$UI_PORT")" ]; then
  say "Something is already on :${UI_PORT} — assuming the React UI is up; reusing it."
elif ! command -v npm >/dev/null 2>&1; then
  warn "npm not found — skipping the React UI (install Node.js to use it)."
else
  if [ ! -d "ui/node_modules" ]; then
    say "Installing React UI dependencies (first run only)…"
    ( cd ui && npm install --silent ) || warn "npm install failed — the UI may not start."
  fi
  say "Starting the React UI (Vite) on :${UI_PORT}…"
  # vite is launched directly (not via npm) so UI_PID is the real server and Ctrl+C
  # stops it cleanly. VITE_API_BASE points the UI at THIS gateway.
  ( cd ui && VITE_API_BASE="http://localhost:${GATEWAY_PORT}" \
             VITE_TEMPORAL_BASE="${AWCP_TEMPORAL_UI_BASE}" \
      exec node_modules/.bin/vite --host --port "${UI_PORT}" ) > "$LOGDIR/ui.log" 2>&1 &
  UI_PID=$!
fi

# ── 8. the AWCP Gateway (foreground) ──────────────────────────────────
echo
echo "  ── AWCP is up — open the React UI ───────────────────────────────"
echo "  ▶  React UI (give prompts) : http://localhost:${UI_PORT}"
echo
echo "     Gateway (API/backend)  : http://localhost:${GATEWAY_PORT}"
echo "     Registry dashboard     : http://localhost:${GATEWAY_PORT}/            (Tokens column)"
echo "     Token monitor          : http://localhost:${GATEWAY_PORT}/laminar/ui  (used / remaining bar)"
echo "     User API               : http://localhost:${GATEWAY_PORT}/user/agents · POST /user/submit"
echo "     API docs (all groups)  : http://localhost:${GATEWAY_PORT}/docs"
echo "     Temporal UI (workflows): ${TEMPORAL_UI}   (queues: $AGENT_RADAR_TASK_QUEUE, $AGENT_EXEC_TASK_QUEUE)"
echo "     Grafana (traces/metrics/logs): http://localhost:3000   (admin / awcp1234)"
echo "     Prometheus             : http://localhost:9090"
if [ -n "${AWCP_OPA_URL:-}" ]; then
echo "     OPA policy engine      : ${AWCP_OPA_URL}   (gate PDP — ${AWCP_OPA_SHADOW:-false} shadow)"
else
echo "     OPA policy engine      : http://localhost:8181   (running; gate uses policy.py until AWCP_OPA_URL is set — see README)"
fi
echo "     MCP server             : http://localhost:8002   (SSE)"
echo "     Ollama                 : http://localhost:11434"
echo "     agents bundle          : $AWCP_AGENTS_DIR"
[ "${DEMO:-0}" = "1" ] && \
echo "     Demo (DEMO=1)          : synthetic 'demo-writer' walks the token-control loop (~20s after boot)."
if [ -n "${LMNR_PROJECT_API_KEY:-}" ]; then
echo "     Laminar dashboard      : http://localhost:5667   (open the PROJECT whose API key is in .env — traces/tokens land there)"
echo "     Laminar export         : ON → ${LMNR_OTLP_ENDPOINT}   (LLM/token spans only; set LMNR_EXPORT_ONLY_LLM=false to send every span)"
else
echo "     Laminar export         : off (set LMNR_PROJECT_API_KEY in .env to dual-export token spans to :5667)"
fi
echo
echo "  ▶ In the React UI: pick an agent → type a goal → submit → watch the live"
echo "    step timeline (folded from the Temporal workflow). Traces/metrics/logs"
echo "    land in Grafana; activities/workflows show in the Temporal UI."
echo "  ▶ Press Ctrl+C to stop EVERYTHING this script started."
echo "  ──────────────────────────────────────────────────────────────"
echo
./.venv/bin/uvicorn awcp.gateway.app:app --host 0.0.0.0 --port "${GATEWAY_PORT}"
