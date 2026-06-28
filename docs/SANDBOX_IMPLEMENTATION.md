# Sandboxing the Workspace Tools with OpenSandbox

## Why

The MCP server (`src/awcp/mcp/server.py`) exposes three "workspace" tools to any
connected agent — `read_file`, `write_file`, `run_command`. Before this change they
operated directly on the host: `open()`/`os.makedirs()` against the server's own working
directory, and `run_command` ran arbitrary shell via `asyncio.create_subprocess_shell()`
with **zero isolation**. Any MCP client (or a compromised/misbehaving agent) could read,
write, or execute anything the server process itself had permission to touch — including
the rest of this repo, `.env` secrets, and the host filesystem beyond it.

The fix: run those three tools inside a single, long-lived, **OpenSandbox**-managed
container, with only a dedicated host directory (`workspace/`) bind-mounted in. Agents
get a real Linux environment to read/write/execute in — just not anywhere near the host.

## Architecture

```
MCP client (agent / Claude / MCP Inspector)
        │  SSE  (tool call: read_file / write_file / run_command)
        ▼
awcp.mcp.server  (uvicorn :8002)
        │  opensandbox Python SDK
        ▼
OpenSandbox runtime  (local control-plane process, :8080)
        │  docker (the runtime's own backend, configured in ~/.sandbox.toml)
        ▼
Docker container  (python:3.11, /workspace ← bind-mount → host workspace/)
```

Two important properties of this layering:

- **Our code never touches Docker directly.** `src/awcp/runtime/sandbox.py` only imports
  from the `opensandbox` package (`Sandbox`, `Volume`, `Host`, `ConnectionConfig`). The
  OpenSandbox *server* happens to use Docker as its execution backend on this machine
  (`[runtime] type = "docker"` in `~/.sandbox.toml`) — it could just as easily be
  Kubernetes, with no code change on our side.
- **The sandbox is a process-local singleton, not a global service.** It lives in
  whichever process imports `awcp.mcp.server` (normally the uvicorn process on `:8002`).
  Restarting that process resets the sandbox to `not_started` — that's expected, not a bug.

## What got built

### 1. `src/awcp/runtime/sandbox.py` (new)

Owns the sandbox's entire lifecycle:

- `get_sandbox()` — lazily creates the container on first use (an `asyncio.Lock` guards
  concurrent first-callers), bind-mounting `WORKSPACE_HOST_DIR` (default
  `<repo>/workspace`, override via `AWCP_WORKSPACE_DIR`) to `/workspace` inside the
  container. Connects to the OpenSandbox runtime at `127.0.0.1:8080` explicitly (see
  [Issue #1](#issue-1-localhost-resolved-to-the-wrong-service) below for why not
  `"localhost"`).
- `close_sandbox()` — kills and tears down the container. Wired into the MCP server's
  ASGI shutdown so nothing leaks when the process stops.
- `sandbox_status()` — reports `running` / `not_started` without creating one (lazy init
  is a healthy state, not a failure).
- `record_event()` / `sandbox_events()` — an in-memory ring buffer (same pattern as the
  radar's `_EVENTS` deque) recording lifecycle events (`sandbox_create`, `sandbox_ready`,
  `sandbox_close`) and tool-call outcomes, for the UI's execution-flow timeline.

### 2. `src/awcp/mcp/server.py`

- `read_file` / `write_file` / `run_command` now call `get_sandbox()` and operate via
  `sandbox.files.read_file/write_file` and `sandbox.commands.run`, instead of local
  `open()`/`subprocess`. The existing `".."` / leading-`/` path guard is kept as a
  first-line check; paths are then resolved inside `/workspace`.
- Every tool call records an event (success, blocked path, or error) via
  `record_sandbox_event()`.
- New routes on the MCP server's Starlette app: `GET /sandbox/status`,
  `GET /sandbox/events`.
- The FastMCP-provided lifespan is wrapped (Starlette removed `add_event_handler` /
  `on_shutdown` in favor of the lifespan context manager) so `close_sandbox()` always
  runs on shutdown.

### 3. `src/awcp/radar/api.py` (the gateway)

The UI only ever talks to the gateway (`:8000`), never directly to the MCP server
(`:8002`) — so the gateway proxies the sandbox's state:

- `/healthz` gained a `"sandbox"` field — a fail-safe `httpx` call to the MCP server's
  `/sandbox/status`. An unreachable MCP server reports `"unreachable"`, never raises;
  `/healthz` must never break because the sandbox is down.
- `GET /sandbox/events` proxies the MCP server's event timeline the same way.

### 4. UI (`ui/src/`)

- **Sidebar** — a "Sandbox" connection row next to Temporal/OTel/Laminar, plus a
  "Sandbox" nav entry (`▣`).
- **`pages/Sandbox.jsx`** (new) — status cards (Status / Image / Sandbox ID / Mount path)
  and a live table of the execution-flow timeline (event kind, detail, time-ago),
  polling the existing `usePoll` hook at the standard interval. Reuses the existing
  `Panel`/`Table`/`Badge`/`StatCard` primitives — no new UI components were introduced.

## Setup: running it locally

### Prerequisites

- **Docker Desktop** running (the OpenSandbox runtime uses Docker as its backend here).
- **uv / uvx** (`brew install uv`) to run the OpenSandbox server without installing it
  into this repo's venv.
- `opensandbox` added to `requirements.txt` (the Python SDK).

### 1 — Start the local OpenSandbox runtime

This is a **separate, one-time-per-session process** — it's the control-plane the SDK
talks to, not something this codebase starts for you.

```bash
uvx opensandbox-server@0.1.13 init-config ~/.sandbox.toml --example docker   # first time only
OPENSANDBOX_INSECURE_SERVER=YES uvx opensandbox-server@0.1.13 --config ~/.sandbox.toml
```

`OPENSANDBOX_INSECURE_SERVER=YES` skips API-key auth for local dev — fine on localhost,
not something to carry into a shared/production deployment.

One required edit to the generated `~/.sandbox.toml`: the `[storage] allowed_host_paths`
allowlist defaults to `[]`, which — despite the file's own comment — means **deny all**,
not allow all. Add this repo's workspace dir explicitly:

```toml
[storage]
allowed_host_paths = ["/absolute/path/to/awcp-mcp-temp/workspace"]
```

### 2 — Start the MCP server (and the rest of the platform as usual)

```bash
PYTHONPATH=src ./.venv/bin/uvicorn awcp.mcp.server:app --host 0.0.0.0 --port 8002
```

(Or via `bash scripts/run_everything.sh`, which starts the gateway/Temporal/etc. — the
MCP server is part of that.)

### 3 — Confirm it's wired up

```bash
curl http://localhost:8002/sandbox/status   # direct from the MCP server
curl http://localhost:8000/healthz          # via the gateway — look for the "sandbox" key
```

Both should report `{"status": "not_started", ...}` before any tool has been called.

## Issues hit during implementation (and why they matter)

These weren't bugs in the sandbox design — they were real environment/SDK gaps worth
knowing about if this breaks again later.

### Issue #1: `localhost` resolved to the wrong service

`opensandbox`'s `ConnectionConfig` defaults to `localhost:8080`. On this machine,
`localhost` resolved to `::1` (IPv6) first, which happened to be **Docker Desktop's own
dashboard**, also listening on `:8080` — not the OpenSandbox runtime (bound to IPv4
`127.0.0.1:8080`). Every sandbox-creation call silently hit Docker Desktop's UI instead,
which has its own CSRF protection, producing `"missing csrf token in request header"`
errors that looked like an OpenSandbox auth problem but weren't.

**Fix:** `sandbox.py` pins the connection explicitly —
`ConnectionConfig(domain="127.0.0.1:8080")` — rather than relying on `"localhost"`
resolution order, which is host-dependent and not something this code should assume.

### Issue #2: empty `allowed_host_paths` means deny-all, not allow-all

The generated `~/.sandbox.toml` ships with `allowed_host_paths = []` and a comment
claiming "if empty, all host paths are allowed." In the running server, an empty list
actually **denies every** bind-mount path (`HOST_PATH_NOT_ALLOWED`). The workspace
directory has to be added explicitly — see [Setup](#setup-running-it-locally) above.

### Issue #3: the mentor's reference snippet's `volumes=` shape doesn't match the SDK

The original snippet used `volumes={host_folder: sandbox_folder}` (a plain dict). The
installed SDK (`opensandbox` 0.1.13) actually wants a list of `Volume` objects:

```python
Volume(name="workspace", host=Host(path=WORKSPACE_HOST_DIR), mount_path="/workspace")
```

### Issue #4: Docker Desktop being stopped looks like a sandbox bug

If Docker Desktop itself isn't running, `Sandbox.create()` fails with a confusing
client-side error (`Expecting value: line 1 column 1 (char 0)` — the SDK trying to parse
an empty error body). The OpenSandbox server log shows the real cause:
`requests.exceptions.ConnectionError ... docker.sock ... no such file or directory`.
**Always check `docker info` first** when sandbox creation fails unexpectedly.

## Verifying the sandbox works (and doesn't touch the host)

A quick battery worth re-running any time this code changes, before pushing:

1. **Status before any call** — fresh MCP server process reports `not_started`.
2. **Write + read round-trip** — `write_file(path="probe.txt", ...)` then
   `read_file(path="probe.txt")`; confirm the file appears **only** at
   `workspace/probe.txt` on the host (`find / -name probe.txt` should find nothing else).
3. **Path traversal guard** — `read_file(path="../../etc/passwd")` →
   `"Error: Invalid path."`.
4. **Container isolation** — `run_command("whoami && id && cat /etc/hostname && ls /")`
   should show `root`/`uid=0`, a random container hostname, and a bare container
   filesystem — nothing resembling the real host.
5. **Blast radius** — `run_command("rm -rf /workspace/*")` should empty `workspace/` and
   touch nothing else (`git status` in the repo should show no deletions).
6. **Status after activity** — flips to `running` with a real `sandbox_id`.
7. **Clean teardown** — stopping the MCP server process should remove the sandbox
   container (`docker ps` empty afterward); check the server log for
   `sandbox.close` → `DELETE /v1/sandboxes/...` → `204`.

All of the above can be driven with a small MCP client script
(`mcp.client.sse.sse_client` + `ClientSession.call_tool(...)`) against
`http://localhost:8002/sse` — no need for a full agent.

## Configuration reference

| Env var | Default | Purpose |
|---|---|---|
| `AWCP_WORKSPACE_DIR` | `<cwd>/workspace` | Host directory bind-mounted into the sandbox |
| `AWCP_SANDBOX_IMAGE` | `python:3.11` | Container base image |
| `OPEN_SANDBOX_DOMAIN` | `127.0.0.1:8080` | OpenSandbox runtime address (SDK side) |
| `AWCP_SANDBOX_EVENTS_MAX` | `200` | Max events kept in the in-memory timeline |
| `AWCP_MCP_URL` | `http://localhost:8002/sse` | Used by the gateway to derive the MCP server's `/sandbox/status` and `/sandbox/events` URLs |
| `AWCP_MCP_STATUS_TIMEOUT` | `2` (seconds) | Gateway → MCP server proxy call timeout |

`~/.sandbox.toml` (consumed by `opensandbox-server`, not by this repo) controls the
runtime backend, resource limits, and `allowed_host_paths` — see
[Issue #2](#issue-2-empty-allowed_host_paths-means-deny-all-not-allow-all).
