# AWCP Agent Console (React)

A self-contained dashboard for the AWCP gateway: pick an agent, send a prompt,
and watch every execution step (LLM call, web search, tool call, synthesize…)
appear **live** as the agent runs — then read the formatted result.

This folder is **fully decoupled from the backend**. Delete it and nothing in
`src/awcp` changes; it only talks to the gateway over HTTP.

## What it shows (all dynamic — nothing hardcoded)

- **Agents** come from `GET /user/agents` — whatever agents the bundle exposes,
  with their framework / model / tools / examples and live running state.
- **Timeline** comes from `GET /user/status/...` which reads the task's Temporal
  workflow history back: one item per activity the agent actually triggered. A
  new step type the backend starts emitting renders automatically.
- **Result / tools / governed writes** are whatever the agent returned.

## Run

Requires the gateway running on `:8000` (`./scripts/run_gateway.sh`).

```bash
cd ui
npm install
npm run dev          # http://localhost:5173
```

Point at a non-default gateway:

```bash
VITE_API_BASE=http://localhost:8000 npm run dev
```

Production build (static files in `dist/`):

```bash
npm run build && npm run preview
```

## How it works

```
[React] --POST /user/submit-->   [Gateway] --start--> agent /tasks   (agent self-instruments OTel)
[React] --GET  /user/status-->   [Gateway] --reads--> Temporal history (one activity per step)
```

The gateway never blocks the UI: `submit` returns immediately with ids, and the
dashboard polls `status` ~once a second until the task settles. High-risk writes
that pause for approval surface Approve / Deny buttons (proxied via
`POST /user/approve/...`).
