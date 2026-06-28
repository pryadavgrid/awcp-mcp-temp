import { usePoll } from '../hooks/usePoll.js'
import { getWorkflows } from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { Badge, StatusBadge } from '../components/Badge.jsx'
import { StatCard } from '../components/StatCard.jsx'
import { fmtDuration, timeAgo } from '../lib/format.js'

// Status comes straight from Temporal now (running / completed / terminated /
// canceled / failed / timed_out) — never guessed from timestamps — so a
// terminated run reads as "terminated", not "completed". Fully dynamic.
const prettyType = (t) => {
  const s = String(t || '')
  if (/exec/i.test(s)) return 'Task Execution'
  if (/onboard/i.test(s)) return 'Agent Onboarding'
  return s || 'Workflow'
}

export default function Workflows() {
  const { data, loading } = usePoll(getWorkflows, [])
  const rows = data?.workflows || []
  const c = data?.counts || {}
  const terminatedCanceled = (c.terminated || 0) + (c.canceled || 0)
  const failed = (c.failed || 0) + (c.timed_out || 0)

  return (
    <div className="space-y-6">
      {/* Summary boxes: how many workflows are running / completed / terminated. */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Running" value={c.running ?? 0} accent="amber" />
        <StatCard label="Completed" value={c.completed ?? 0} accent="emerald" />
        <StatCard label="Terminated / Canceled" value={terminatedCanceled} accent="rose" />
        <StatCard
          label="Failed"
          value={failed}
          accent="rose"
          sub={c.timed_out ? `incl. ${c.timed_out} timed out` : undefined}
        />
      </div>

      <Panel
        title="Workflows"
        subtitle="Live from Temporal — real status for every onboarding & task-execution workflow; click an id to open it in Temporal"
        right={<span className="text-xs text-slate-500">{c.total ?? rows.length} total</span>}
      >
        <Table columns={['Workflow ID', 'Type', 'Status', 'Started', 'Duration']}>
          {loading && !data ? (
            <EmptyRow colSpan={5}>Loading workflows…</EmptyRow>
          ) : rows.length === 0 ? (
            <EmptyRow colSpan={5}>
              {data?.error ? `Temporal: ${data.error}` : 'No workflows yet.'}
            </EmptyRow>
          ) : (
            rows.map((w) => (
              <tr key={w.workflow_id} className="hover:bg-slate-50">
                <Td>
                  {w.temporal_url ? (
                    <a
                      href={w.temporal_url}
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
                  <Badge tone={/exec/i.test(w.type) ? 'violet' : 'blue'}>{prettyType(w.type)}</Badge>
                </Td>
                <Td>
                  <StatusBadge value={w.status} />
                </Td>
                <Td className="text-slate-500">{w.start_ts ? timeAgo(w.start_ts) : '—'}</Td>
                <Td className="text-slate-500">
                  {w.duration != null
                    ? fmtDuration(w.duration)
                    : w.status === 'running'
                      ? 'running…'
                      : '—'}
                </Td>
              </tr>
            ))
          )}
        </Table>
      </Panel>
    </div>
  )
}
