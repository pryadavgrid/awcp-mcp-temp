# AWCP Dashboard

React + Vite + Tailwind control-plane UI for the AWCP gateway.

Four views (left menu):

| View              | Source endpoints                                              |
| ----------------- | ------------------------------------------------------------- |
| **Dashboard**     | `/healthz`, `/agents`, `/events` — fleet counters, recent workflows, live tool/gate decisions |
| **Radar**         | `/agents` — every detected/registered agent                   |
| **Workflow**      | `/agents` (onboarding) + `/laminar/usage[/{id}]` (task execution) — each row deep-links to the Temporal Web UI |
| **Token Monitor** | `/laminar/usage`, `/laminar/budgets`, `POST /laminar/reset/{id}` — per-agent usage/budget/cost, reset window, link to the Laminar dashboard |

Everything rendered is **live data from the gateway** — nothing is hardcoded.

## Decoupling contract

This folder is a pure frontend: it only calls the gateway over HTTP. **Deleting
`ui/` has zero effect on the backend** — no Python imports it, and the gateway's
CORS is already open (`allow_origins=*` in `src/awcp/gateway/app.py`).

## Run

`scripts/run_everything.sh` starts this automatically on **:5173** with
`VITE_API_BASE` pointed at the gateway. Standalone:

```bash
cd ui
npm install
npm run dev      # http://localhost:5173
```

## Configuration (Vite env vars, all optional)

| Var                  | Default                  | Purpose                          |
| -------------------- | ------------------------ | -------------------------------- |
| `VITE_API_BASE`      | `http://localhost:8000`  | AWCP gateway base URL            |
| `VITE_TEMPORAL_BASE` | `http://localhost:8233`  | Temporal Web UI (workflow links) |
| `VITE_LAMINAR_URL`   | `http://localhost:5667/` | Official Laminar dashboard link  |
| `VITE_POLL_MS`       | `4000`                   | Live-refresh interval (ms)       |
