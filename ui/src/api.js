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
// The bundle agents + their live tool catalogs (folder id, registry agent_id, tools).
export const getUserAgents = () => call('GET', '/user/agents')
export const getEvents = (limit = 50) => call('GET', `/events?limit=${limit}`)
export const setAutonomy = (id, profile) =>
  call('POST', `/agents/${encodeURIComponent(id)}/autonomy`, { profile })

// ── context graph (governed-step trail) ──────────────────────────────────────
// Every governed step (tool call, route, generate) recorded as a tamper-chained
// node in evidence.ledger. The global feed drives the Context Graph view; the
// per-run / per-agent endpoints are available for drill-downs.
export const getContextFeed = (limit = 200) => call('GET', `/context-graph?limit=${limit}`)
export const getWorkflowGraph = (wf) =>
  call('GET', `/context-graph/${encodeURIComponent(wf)}`)
export const getAgentGraph = (id) =>
  call('GET', `/agents/${encodeURIComponent(id)}/context-graph`)
// Whole-ledger hash-chain verification (re-hash + linkage). {enabled:false} when
// the durable ledger (Postgres) is off — nothing persisted to verify.
export const getChainVerify = () => call('GET', '/context-graph/verify')

// Neo4j graph projection (additive read-model). {enabled:false} when Neo4j is off.
export const getNeo4jStatus = () => call('GET', '/context-graph/neo4j/status')
export const getNeo4jGraph = (workflow) =>
  call('GET', `/context-graph/neo4j/graph${workflow ? `?workflow=${encodeURIComponent(workflow)}` : ''}`)
export const backfillNeo4j = () => call('POST', '/context-graph/neo4j/backfill')

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

// ── agent hooks ───────────────────────────────────────────────────────────────
// Registered hooks + per-hook stats, the recent-events ring buffer, and the
// live enable/disable toggles. Served by the gateway when the
// src/awcp/agent_hooks package is mounted; a 404 means it isn't.
export const getHooks = () => call('GET', '/hooks')
export const getHooksRecent = (limit = 60) => call('GET', `/hooks/recent?limit=${limit}`)
export const enableHook = (name) => call('POST', `/hooks/${encodeURIComponent(name)}/enable`)
export const disableHook = (name) => call('POST', `/hooks/${encodeURIComponent(name)}/disable`)
// Policy-guard: enable/configure it (deny-list) at runtime, and one-click test it.
export const getGuard = () => call('GET', '/hooks/guard')
export const setGuard = (denyTools, enabled = true) =>
  call('POST', '/hooks/guard', { deny_tools: denyTools, enabled })
export const testGuard = (agentId, action) =>
  call('POST', '/hooks/guard/test', { agent_id: agentId, action })

// ── tool risk tiers (the hidden SLM OPA agent, via the gateway proxy) ──────────
// The SLM-reasoned tier vocabulary + block set + per-tool tiers + the recent
// tool-call decisions (newest first) the Radar renders as tier bars. Returns an
// inert { enabled:false } shape when no OPA agent is wired, so the Radar degrades.
export const getToolTiers = () => call('GET', '/opa/tiers')
// Operator slider: set the single block threshold. Any tool call whose SLM tier is
// at or above this tier blocks the question in the user UI. Persisted by the OPA agent.
export const setBlockThreshold = (threshold) =>
  call('POST', '/opa/threshold', { threshold })

// Build a Temporal Web UI deep link for any workflow id.
export const temporalUrl = (wfId) =>
  `${TEMPORAL_BASE}/namespaces/default/workflows/${encodeURIComponent(wfId)}`
