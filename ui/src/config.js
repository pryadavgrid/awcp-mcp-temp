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

// Neo4j Browser (the graph DB's own UI). Linked from the Context Graph "Graph"
// view so a run's projection can be opened/queried natively. Override with
// VITE_NEO4J_URL (e.g. a remote bolt host's browser).
export const NEO4J_BROWSER_URL = strip(import.meta.env.VITE_NEO4J_URL || 'http://localhost:7474')

// Build a Neo4j Browser deep link. With a workflow id, it pre-loads a Cypher
// scoped to that run into the editor (matches the projection's labels:
// (:Agent)-[:PERFORMED]->(:Step {workflow_id})-[:USED]->(:Tool), NEXT lineage).
export function neo4jBrowserUrl(workflow) {
  const base = `${NEO4J_BROWSER_URL}/browser/`
  if (!workflow) return base
  const wf = String(workflow).replace(/\\/g, '\\\\').replace(/'/g, "\\'")
  const cypher =
    `MATCH (s:Step {workflow_id: '${wf}'})\n` +
    `OPTIONAL MATCH (a:Agent)-[:PERFORMED]->(s)\n` +
    `OPTIONAL MATCH (s)-[:USED]->(t:Tool)\n` +
    `OPTIONAL MATCH (s)-[:NEXT]->(s2:Step)\n` +
    `RETURN *`
  return `${base}?cmd=edit&arg=${encodeURIComponent(cypher)}`
}

// How often (ms) the views re-poll the gateway for live data.
export const POLL_MS = Number(import.meta.env.VITE_POLL_MS || 4000)
