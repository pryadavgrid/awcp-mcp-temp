import { usePoll } from '../hooks/usePoll.js'
import { getHealth, getSandboxEvents } from '../api.js'
import { StatCard } from '../components/StatCard.jsx'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { Badge, toneFor } from '../components/Badge.jsx'
import { timeAgo, prettyKind, shortId } from '../lib/format.js'

const load = async () => {
  const [health, sandboxEvents] = await Promise.all([getHealth(), getSandboxEvents(50)])
  return { sandbox: health?.sandbox, events: sandboxEvents?.events || [], reachable: sandboxEvents?.reachable }
}

export default function Sandbox() {
  const { data, loading } = usePoll(load, [])
  const sandbox = data?.sandbox
  const events = data?.events || []
  const status = sandbox?.status

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Status"
          value={loading && !data ? '—' : prettyKind(status || 'unknown')}
          sub={status === 'running' ? 'container alive, isolated to workspace/' : status === 'not_started' ? 'lazy init — created on first tool call' : sandbox?.reason || '—'}
          accent={status === 'running' ? 'emerald' : status === 'unreachable' ? 'rose' : 'slate'}
        />
        <StatCard label="Image" value={sandbox?.image || '—'} sub="container base image" accent="indigo" />
        <StatCard
          label="Sandbox ID"
          value={sandbox?.sandbox_id ? shortId(sandbox.sandbox_id, 12) : '—'}
          sub={sandbox?.sandbox_id ? 'current container' : 'no container yet'}
          accent="violet"
        />
        <StatCard
          label="Mount"
          value={sandbox?.mount_path || '—'}
          sub={sandbox?.workspace_dir || 'host workspace directory'}
          accent="slate"
        />
      </div>

      <Panel
        title="Sandbox Execution Flow"
        subtitle="Lifecycle + tool-call timeline (read_file / write_file / run_command) — newest first"
      >
        <Table columns={['Event', 'Detail', 'When']}>
          {events.length === 0 ? (
            <EmptyRow colSpan={3}>
              {data?.reachable === false ? 'MCP server unreachable.' : 'No sandbox activity yet — call a workspace tool to see it here.'}
            </EmptyRow>
          ) : (
            events.map((e, i) => (
              <tr key={`${e.ts}-${i}`} className="hover:bg-slate-50">
                <Td>
                  <Badge tone={toneFor(e.kind)}>{prettyKind(e.kind)}</Badge>
                </Td>
                <Td>
                  <span className="font-mono text-xs text-slate-700">{e.detail || '—'}</span>
                </Td>
                <Td className="text-slate-500">{timeAgo(e.ts)}</Td>
              </tr>
            ))
          )}
        </Table>
      </Panel>
    </div>
  )
}
