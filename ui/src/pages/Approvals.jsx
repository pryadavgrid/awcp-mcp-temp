import { useState } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import { getApprovals, decideApproval } from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { Badge, StatusBadge } from '../components/Badge.jsx'
import { StatCard } from '../components/StatCard.jsx'
import { timeAgo, shortId } from '../lib/format.js'

// Pull pending writes (what needs a decision) and the recent history together, so
// the operator sees the queue plus an audit of what was already approved/denied.
const load = async () => {
  const [pending, recent] = await Promise.all([
    getApprovals('pending', 100),
    getApprovals('', 50).catch(() => []),
  ])
  return { pending, recent }
}

const RISK_TONE = { high: 'red', medium: 'amber', low: 'slate', critical: 'red' }

export default function Approvals() {
  const { data, error, loading, refresh } = usePoll(load, [])
  const [busy, setBusy] = useState(null) // id currently being decided
  const [note, setNote] = useState(null) // {tone, text} flash message

  const decide = async (row, decision) => {
    setBusy(row.id)
    setNote(null)
    try {
      const res = await decideApproval(row.id, decision)
      const releasedNote = res?.released
        ? 'agent released'
        : 'recorded (agent not reachable to release)'
      setNote({
        tone: decision === 'approve' ? 'green' : 'amber',
        text: `${decision === 'approve' ? 'Approved' : 'Denied'} “${row.action}” — ${releasedNote}.`,
      })
      await refresh()
    } catch (e) {
      setNote({ tone: 'red', text: e?.message || String(e) })
    } finally {
      setBusy(null)
    }
  }

  const pending = data?.pending || []
  const recent = (data?.recent || []).filter((r) => r.status !== 'pending')

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <StatCard label="Awaiting approval" value={pending.length} />
        <StatCard
          label="Approved (recent)"
          value={recent.filter((r) => r.status === 'approved').length}
        />
        <StatCard
          label="Denied (recent)"
          value={recent.filter((r) => r.status === 'denied').length}
        />
      </div>

      {note && (
        <div
          className={`rounded-lg border px-4 py-3 text-sm ${
            note.tone === 'red'
              ? 'border-rose-200 bg-rose-50 text-rose-700'
              : note.tone === 'green'
                ? 'border-brand-200 bg-brand-50 text-brand-700'
                : 'border-amber-200 bg-amber-50 text-amber-800'
          }`}
        >
          {note.text}
        </div>
      )}

      <Panel
        title="Pending write approvals"
        subtitle="Agents pause here before any write action — approve to let it proceed, deny to block it"
      >
        {error && !data ? (
          <div className="px-5 py-10 text-center text-sm text-slate-500">
            Cannot load approvals — {error}
          </div>
        ) : (
          <Table columns={['Agent', 'Wants to', 'Risk', 'Task', 'Requested', 'Decision']}>
            {pending.length === 0 ? (
              <EmptyRow colSpan={6}>
                {loading ? 'Loading…' : 'Nothing waiting — no agent is requesting a write right now.'}
              </EmptyRow>
            ) : (
              pending.map((row) => (
                <tr key={row.id}>
                  <Td>
                    <div className="font-medium text-brand-900">
                      {row.agent_name || row.agent_id || '—'}
                    </div>
                    {row.agent_name && row.agent_id && (
                      <div className="font-mono text-[11px] text-slate-400">{row.agent_id}</div>
                    )}
                  </Td>
                  <Td>
                    <div className="font-mono text-xs text-slate-700">{row.action || '—'}</div>
                    {row.detail && row.detail !== row.action && (
                      <div className="text-[11px] text-slate-500">{row.detail}</div>
                    )}
                  </Td>
                  <Td>
                    {row.risk ? (
                      <Badge tone={RISK_TONE[String(row.risk).toLowerCase()] || 'slate'}>
                        {row.risk}
                      </Badge>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </Td>
                  <Td>
                    <span className="font-mono text-[11px] text-slate-500">
                      {shortId(row.task_id, 12)}
                    </span>
                  </Td>
                  <Td>
                    <span className="text-xs text-slate-500">{timeAgo(row.ts)}</span>
                  </Td>
                  <Td>
                    <div className="flex gap-2">
                      <button
                        disabled={busy === row.id}
                        onClick={() => decide(row, 'approve')}
                        className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-brand-700 disabled:opacity-50"
                      >
                        {busy === row.id ? '…' : 'Approve'}
                      </button>
                      <button
                        disabled={busy === row.id}
                        onClick={() => decide(row, 'deny')}
                        className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 ring-1 ring-inset ring-rose-200 transition hover:bg-rose-50 disabled:opacity-50"
                      >
                        Deny
                      </button>
                    </div>
                  </Td>
                </tr>
              ))
            )}
          </Table>
        )}
      </Panel>

      <Panel title="Recent decisions" subtitle="Audit trail of approved / denied writes (persisted)">
        <Table columns={['Agent', 'Action', 'Risk', 'Status', 'By', 'When']}>
          {recent.length === 0 ? (
            <EmptyRow colSpan={6}>No decisions yet.</EmptyRow>
          ) : (
            recent.map((row) => (
              <tr key={row.id}>
                <Td>{row.agent_name || row.agent_id || '—'}</Td>
                <Td>
                  <span className="font-mono text-xs text-slate-700">{row.action || '—'}</span>
                </Td>
                <Td>
                  {row.risk ? (
                    <Badge tone={RISK_TONE[String(row.risk).toLowerCase()] || 'slate'}>
                      {row.risk}
                    </Badge>
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </Td>
                <Td>
                  <StatusBadge value={row.status} />
                </Td>
                <Td>
                  <span className="text-xs text-slate-500">{row.decided_by || '—'}</span>
                </Td>
                <Td>
                  <span className="text-xs text-slate-500">
                    {timeAgo(row.decided_at || row.ts)}
                  </span>
                </Td>
              </tr>
            ))
          )}
        </Table>
      </Panel>
    </div>
  )
}
