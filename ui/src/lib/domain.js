// Domain helpers that turn raw gateway payloads into the rows the views render.
// Everything here is derived from live API data — no fixed agent/tool/workflow lists.

// The next rung an agent would drop to on its next breach (from effective_ladder).
export function nextRung(agent) {
  const ladder = agent?.effective_ladder || agent?.autonomy_ladder || []
  const i = ladder.indexOf(agent?.autonomy_profile)
  if (i >= 0 && i < ladder.length - 1) return ladder[i + 1]
  return null
}

// Onboarding workflows come straight off /agents: each agent carries its
// onboarding_workflow_id + onboarding_state + a ready-made temporal_url.
export function onboardingWorkflows(agents = []) {
  return agents
    .filter((a) => a.onboarding_workflow_id)
    .map((a) => ({
      key: a.onboarding_workflow_id,
      workflow_id: a.onboarding_workflow_id,
      type: 'Agent Onboarding',
      status: a.onboarding_state || (a.status === 'active' ? 'done' : 'pending'),
      agent: a.name,
      agent_id: a.id,
      tool_calls: Array.isArray(a.capabilities) ? a.capabilities.length : 0,
      duration: null,
      url: a.temporal_url || null,
      ts: a.first_seen || 0,
    }))
}

// Build a lookup id -> display name from /agents (used to label events/usage).
export function agentNameMap(agents = []) {
  const m = {}
  for (const a of agents) m[a.id] = a.name || a.id
  return m
}

// Event kinds that represent a tool / action decision (the "running tools" feed).
export const TOOL_EVENT_KINDS = new Set([
  'gate',
  'token_hard_stop',
  'token_warn',
  'token_exhausted',
  'token_uncontrolled',
  'token_process_stop',
  'token_remote_stop',
  'token_remote_stop_failed',
  'degraded',
  'degraded_step',
  'gateway_bypass',
])
