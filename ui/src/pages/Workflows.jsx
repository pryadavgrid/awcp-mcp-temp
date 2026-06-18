import { usePoll } from '../hooks/usePoll.js'
import { getAgents, getUsage, getUsageOne, temporalUrl } from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { Badge, StatusBadge } from '../components/Badge.jsx'
import { fmtDuration, timeAgo } from '../lib/format.js'
import { onboardingWorkflows } from '../lib/domain.js'

// A task-execution workflow has no dedicated list endpoint, so we reconstruct it
// from the token ledger: every recorded LLM/tool call carries (agent_id, task_id,
// ts), and the gateway names the workflow `task-<agent_id>-<task_id>`. Grouping
// the per-agent recent calls by task_id yields the same workflows Temporal ran —
// fully dynamic, nothing hardcoded.
async function loadWorkflows() {
  const [agents, usage] = await Promise.all([getAgents(), getUsage().catch(() => [])])

  const onboarding = onboardingWorkflows(agents)

  const details = await Promise.all(
    (usage || []).map((u) =>
      getUsageOne(u.agent_id)
        .then((d) => ({ u, d }))
        .catch(() => null),
    ),
  )

  const now = Date.now() / 1000
  const exec = []
  for (const item of details) {
    if (!item) continue
    const { u, d } = item
    const byTask = {}
    for (const r of d.recent || []) {
      const tid = r.task_id || 'unknown'
      ;(byTask[tid] ||= []).push(r)
    }
    for (const [tid, recs] of Object.entries(byTask)) {
      const tss = recs.map((r) => r.ts).filter(Boolean)
      const min = tss.length ? Math.min(...tss) : 0
      const max = tss.length ? Math.max(...tss) : 0
      const wfId = `task-${u.agent_id}-${tid}`
      exec.push({
        key: wfId,
        workflow_id: wfId,
        type: 'Task Execution',
        status: now - max < 60 ? 'running' : 'complete',
        agent: u.name || u.agent_id,
        agent_id: u.agent_id,
        tool_calls: recs.length,
        duration: max - min,
        url: temporalUrl(wfId),
        ts: max,
      })
    }
  }

  return [...exec, ...onboarding].sort((a, b) => (b.ts || 0) - (a.ts || 0))
}

export default function Workflows() {
  const { data, loading } = usePoll(loadWorkflows, [])
  const rows = data || []

  return (
    <Panel
      title="Workflows"
      subtitle="Agent-onboarding & task-execution workflows — click an id to open it in Temporal"
      right={
        <span className="text-xs text-slate-500">
          {rows.length} workflow{rows.length === 1 ? '' : 's'}
        </span>
      }
    >
      <Table columns={['Workflow ID', 'Type', 'Status', 'Agent', 'Tool Calls', 'Total Time']}>
        {loading && !data ? (
          <EmptyRow colSpan={6}>Loading workflows…</EmptyRow>
        ) : rows.length === 0 ? (
          <EmptyRow colSpan={6}>No workflows yet.</EmptyRow>
        ) : (
          rows.map((w) => (
            <tr key={w.key} className="hover:bg-slate-50">
              <Td>
                {w.url ? (
                  <a
                    href={w.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-xs font-semibold text-brand-600 hover:text-brand-700 hover:underline"
                    title="Open in Temporal"
                  >
                    {w.workflow_id} ↗
                  </a>
                ) : (
                  <span className="font-mono text-xs text-brand-900">{w.workflow_id}</span>
                )}
              </Td>
              <Td>
                <Badge tone={w.type === 'Task Execution' ? 'violet' : 'blue'}>{w.type}</Badge>
              </Td>
              <Td>
                <StatusBadge value={w.status} />
              </Td>
              <Td>
                <div className="text-slate-800">{w.agent}</div>
                <div className="font-mono text-[11px] text-slate-400">{w.agent_id}</div>
              </Td>
              <Td className="text-slate-700">{w.tool_calls}</Td>
              <Td className="text-slate-500">
                {w.duration != null ? fmtDuration(w.duration) : timeAgo(w.ts)}
              </Td>
            </tr>
          ))
        )}
      </Table>
    </Panel>
  )
}
