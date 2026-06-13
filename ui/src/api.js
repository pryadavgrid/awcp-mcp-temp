// Thin client for the AWCP gateway. Everything here is generic — it never names
// an agent or a tool; the agent list, examples, tools and timeline all come from
// the gateway at runtime, so the UI works for any agent the backend exposes.

const API = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

async function call(method, path, body) {
  const res = await fetch(`${API}${path}`, {
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

export const API_BASE = API

export const listAgents = () => call('GET', '/user/agents')

// Per-agent token usage + budget (the token monitor feed). Optional: returns []
// when the laminar module isn't mounted or no agent has reported usage yet.
export const getUsage = () => call('GET', '/laminar/usage')

export const submitTask = (agent, input) =>
  call('POST', '/user/submit', { agent, input })

export const getStatus = (agent, taskId, workflowId) =>
  call(
    'GET',
    `/user/status/${encodeURIComponent(agent)}/${encodeURIComponent(taskId)}` +
      (workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : ''),
  )

export const approveTask = (agent, taskId, decision) =>
  call(
    'POST',
    `/user/approve/${encodeURIComponent(agent)}/${encodeURIComponent(taskId)}`,
    { decision },
  )
