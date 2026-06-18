// Runtime-configurable endpoints. This UI is a PURE frontend — it only talks to
// the gateway over HTTP, so deleting the whole ui/ folder has zero effect on the
// backend. Override any of these at build/dev time via Vite env vars.

const strip = (s) => String(s || '').replace(/\/+$/, '')

// The AWCP gateway (registry + radar + token monitor). run_everything.sh sets
// VITE_API_BASE to the gateway it started.
export const API_BASE = strip(import.meta.env.VITE_API_BASE || 'http://localhost:8000')

// Temporal Web UI — used to build deep links for task-execution workflows.
// (Onboarding workflows already carry a full `temporal_url` from the gateway.)
export const TEMPORAL_BASE = strip(import.meta.env.VITE_TEMPORAL_BASE || 'http://localhost:8233')

// The official Laminar dashboard (separate process). Linked from Token Monitor.
export const LAMINAR_URL = import.meta.env.VITE_LAMINAR_URL || 'http://localhost:5667/'

// How often (ms) the views re-poll the gateway for live data.
export const POLL_MS = Number(import.meta.env.VITE_POLL_MS || 4000)
