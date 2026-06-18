import { usePoll } from '../hooks/usePoll.js'
import { getAgents, getEvents, getHealth } from '../api.js'
import { StatCard } from '../components/StatCard.jsx'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { Badge, StatusBadge, toneFor } from '../components/Badge.jsx'
import { timeAgo, prettyKind } from '../lib/format.js'
import { agentNameMap, nextRung, onboardingWorkflows, TOOL_EVENT_KINDS } from '../lib/domain.js'

const load = async () => {
  const [health, agents, events] = await Promise.all([getHealth(), getAgents(), getEvents(40)])
  return { health, agents, events }
}

export default function Dashboard() {
  const { data, loading } = usePoll(load, [])
  const health = data?.health
  const agents = data?.agents || []
  const events = data?.events || []
  const names = agentNameMap(agents)

  const agentCount = health?.agent_count ?? agents.length
  const activeAgents = health?.by_autonomy?.active ?? agents.filter((a) => a.autonomy_profile === 'active').length
  const quarantined = health?.quarantined ?? agents.filter((a) => a.status === 'quarantined').length
  const onboardingRunning = agents.filter((a) => a.onboarding_state === 'running').length
  const activeTasks = health?.laminar?.active_tasks ?? 0
  const workflowsRunning = onboardingRunning + activeTasks

  // Recent workflows: onboarding workflows off /agents, newest first, top 5.
  const recent = onboardingWorkflows(agents)
    .sort((a, b) => (b.ts || 0) - (a.ts || 0))
    .slice(0, 5)
  const agentById = Object.fromEntries(agents.map((a) => [a.id, a]))

  // Running tools / actions: the gate + token decision stream.
  const toolEvents = events.filter((e) => TOOL_EVENT_KINDS.has(e.kind)).slice(0, 10)

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Agents Running"
          value={loading && !data ? '—' : agentCount}
          sub={`${activeAgents} active${agentCount ? ` · ${Math.round((activeAgents / agentCount) * 100) || 0}% of fleet` : ''}`}
          accent="indigo"
        />
        <StatCard
          label="Workflows Running"
          value={loading && !data ? '—' : workflowsRunning}
          sub={`${onboardingRunning} onboarding · ${activeTasks} task exec`}
          accent="emerald"
        />
        <StatCard
          label="Tool Calls Running"
          value={loading && !data ? '—' : activeTasks}
          sub="in-flight governed executions"
          accent="violet"
        />
        <StatCard
          label="Quarantined Agents"
          value={loading && !data ? '—' : quarantined}
          sub="held until control hooks observed"
          accent={quarantined > 0 ? 'rose' : 'slate'}
        />
      </div>

      <Panel
        title="Recent Workflows"
        subtitle="Latest onboarding workflows — click an id to open it in Temporal"
      >
        <Table columns={['Workflow ID', 'State', 'Autonomy', 'Next']}>
          {recent.length === 0 ? (
            <EmptyRow colSpan={4}>No workflows yet.</EmptyRow>
          ) : (
            recent.map((w) => {
              const next = nextRung(agentById[w.agent_id])
              return (
                <tr key={w.key} className="hover:bg-slate-50">
                  <Td>
                    <WorkflowLink id={w.workflow_id} url={w.url} />
                    <div className="mt-0.5 text-xs text-slate-500">{w.agent}</div>
                  </Td>
                  <Td>
                    <StatusBadge value={w.status} />
                  </Td>
                  <Td>
                    <StatusBadge value={agentById[w.agent_id]?.autonomy_profile} />
                  </Td>
                  <Td>
                    {next ? (
                      <span className="text-slate-600">{next}</span>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </Td>
                </tr>
              )
            })
          )}
        </Table>
      </Panel>

      <Panel
        title="Running Tools / Actions"
        subtitle="Live gate & token-control decisions — which action, the decision, and the calling agent"
      >
        <Table columns={['Tool / Action', 'Decision', 'Agent', 'When']}>
          {toolEvents.length === 0 ? (
            <EmptyRow colSpan={4}>No tool activity yet.</EmptyRow>
          ) : (
            toolEvents.map((e, i) => (
              <tr key={`${e.ts}-${i}`} className="hover:bg-slate-50">
                <Td>
                  <span className="font-medium text-brand-900">{e.action || prettyKind(e.kind)}</span>
                </Td>
                <Td>
                  <Badge tone={toneFor(e.detail || e.kind)} title={e.detail}>
                    {e.detail || prettyKind(e.kind)}
                  </Badge>
                </Td>
                <Td className="text-slate-700">{names[e.agent_id] || e.agent_id || '—'}</Td>
                <Td className="text-slate-500">{timeAgo(e.ts)}</Td>
              </tr>
            ))
          )}
        </Table>
      </Panel>
    </div>
  )
}

function WorkflowLink({ id, url }) {
  if (!url) return <span className="font-mono text-xs text-brand-900">{id}</span>
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="font-mono text-xs font-semibold text-brand-600 hover:text-brand-700 hover:underline"
      title="Open in Temporal"
    >
      {id} ↗
    </a>
  )
}
