import { usePoll } from '../hooks/usePoll.js'
import { getAgents } from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { StatusBadge } from '../components/Badge.jsx'
import { timeAgo } from '../lib/format.js'

export default function Radar() {
  const { data, loading } = usePoll(getAgents, [])
  const agents = data || []

  return (
    <Panel
      title="Radar — Detected & Registered Agents"
      subtitle="Every agentic environment the radar has scanned or that self-registered"
      right={
        <span className="text-xs text-slate-500">
          {agents.length} agent{agents.length === 1 ? '' : 's'}
        </span>
      }
    >
      <Table
        columns={['Name', 'Kind', 'Framework', 'Status', 'Autonomy', 'Onboarding', 'Owner', 'Live']}
      >
        {loading && !data ? (
          <EmptyRow colSpan={8}>Loading agents…</EmptyRow>
        ) : agents.length === 0 ? (
          <EmptyRow colSpan={8}>No agents detected yet.</EmptyRow>
        ) : (
          agents.map((a) => (
            <tr key={a.id} className="hover:bg-slate-50">
              <Td>
                <div className="font-medium text-brand-900">{a.name}</div>
                <div className="font-mono text-[11px] text-slate-400">{a.id}</div>
              </Td>
              <Td className="text-slate-700">{a.kind || '—'}</Td>
              <Td>
                {a.framework ? (
                  <span className="text-slate-700">{a.framework}</span>
                ) : (
                  <span className="text-slate-400">—</span>
                )}
              </Td>
              <Td>
                <StatusBadge value={a.status} title={a.quarantine_reason || undefined} />
              </Td>
              <Td>
                <StatusBadge value={a.autonomy_profile} title={a.autonomy_reason || undefined} />
              </Td>
              <Td>
                <StatusBadge value={a.onboarding_state || 'pending'} />
              </Td>
              <Td className="text-slate-700">{a.owner || '—'}</Td>
              <Td>
                <span className="flex items-center gap-2">
                  <span
                    className={`h-2 w-2 rounded-full ${a.alive ? 'bg-brand-500' : 'bg-slate-300'}`}
                  />
                  <span className={a.alive ? 'text-brand-600' : 'text-slate-400'}>
                    {a.alive ? 'live' : 'gone'}
                  </span>
                  <span className="text-xs text-slate-400">· {timeAgo(a.last_seen)}</span>
                </span>
              </Td>
            </tr>
          ))
        )}
      </Table>
    </Panel>
  )
}
