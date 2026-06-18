// Thin client for the AWCP gateway. Every value the UI renders comes from these
// endpoints at runtime — nothing is hardcoded. The gateway already enables CORS
// (allow_origins=*), so the dev server on :5173 can call it directly.
import { API_BASE, TEMPORAL_BASE } from './config'

async function call(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const detail = data && data.detail !== undefined ? data.detail : data
    const msg = typeof detail === 'string' ? detail : JSON.stringify(detail)
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return data
}

// ── registry / radar ────────────────────────────────────────────────────────
export const getHealth = () => call('GET', '/healthz')
export const getAgents = () => call('GET', '/agents')
export const getEvents = (limit = 50) => call('GET', `/events?limit=${limit}`)
export const setAutonomy = (id, profile) =>
  call('POST', `/agents/${encodeURIComponent(id)}/autonomy`, { profile })

// ── token monitor (laminar) ──────────────────────────────────────────────────
export const getUsage = () => call('GET', '/laminar/usage')
export const getUsageOne = (id) => call('GET', `/laminar/usage/${encodeURIComponent(id)}`)
export const getBudgets = () => call('GET', '/laminar/budgets')
export const getLaminarStatus = () => call('GET', '/laminar/status')
export const resetWindow = (id) => call('POST', `/laminar/reset/${encodeURIComponent(id)}`)
// Set (or clear) a per-agent token budget override. tokens > 0 sets it; 0 clears
// it so the agent falls back to its risk-tier / system-default budget.
export const setBudget = (id, tokens) =>
  call('POST', `/laminar/budgets/${encodeURIComponent(id)}`, { tokens })

// Build a Temporal Web UI deep link for any workflow id.
export const temporalUrl = (wfId) =>
  `${TEMPORAL_BASE}/namespaces/default/workflows/${encodeURIComponent(wfId)}`
