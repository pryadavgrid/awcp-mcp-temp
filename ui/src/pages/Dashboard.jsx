import { usePoll } from '../hooks/usePoll.js'
import {
  getAgents,
  getEvents,
  getHealth,
  getWorkflows,
  getApprovals,
  getUsage,
  getContextFeed,
  getHooks,
  getPolicy,
} from '../api.js'
import { StatCard } from '../components/StatCard.jsx'
import { DraggableGrid } from '../components/DraggableGrid.jsx'
import { AreaChart, Gauge } from '../components/Charts.jsx'
import { StatusBadge, Badge } from '../components/Badge.jsx'
import { Icon } from '../components/Icons.jsx'
import { timeAgo, fmtInt, pctCapped, prettyKind, shortId } from '../lib/format.js'

// The dashboard is a launchpad: a small slice of EVERY control-plane view, where
// each tile drills into the matching page. One combined poll feeds them all;
// every call is best-effort (.catch) so a single offline service never blanks
// the whole board.
const load = async () => {
  const [health, agents, events, workflows, approvals, usage, context, hooks, policy] =
    await Promise.all([
      getHealth().catch(() => null),
      getAgents().catch(() => []),
      getEvents(40).catch(() => []),
      getWorkflows(50).catch(() => null),
      getApprovals('pending', 50).catch(() => []),
      getUsage().catch(() => []),
      getContextFeed(150).catch(() => null),
      getHooks().catch(() => null),
      getPolicy().catch(() => null),
    ])
  return { health, agents, events, workflows, approvals, usage, context, hooks, policy }
}

export default function Dashboard({ onNavigate }) {
  const { data, loading } = usePoll(load, [], 6000)
  const go = (id) => onNavigate && onNavigate(id)
  const dash = loading && !data ? '—' : undefined

  const health = data?.health
  const agents = data?.agents || []
  const wf = data?.workflows
  const wfCounts = wf?.counts || {}
  const approvals = data?.approvals || []
  const usage = data?.usage || []
  const ctxNodes = data?.context?.nodes || []
  const hooksStatus = data?.hooks?.hooks?.status || data?.hooks?.status || {}
  const policy = data?.policy

  // ── fleet figures ──────────────────────────────────────────────────────────
  const agentCount = health?.agent_count ?? agents.length
  const activeAgents =
    health?.by_autonomy?.active ?? agents.filter((a) => a.autonomy_profile === 'active').length
  const quarantined =
    health?.quarantined ?? agents.filter((a) => a.status === 'quarantined').length
  const restricted = Math.max(0, agentCount - activeAgents - quarantined)
  const onboardingRunning = agents.filter((a) => a.onboarding_state === 'running').length
  const activeTasks = health?.laminar?.active_tasks ?? 0
  const workflowsRunning = wfCounts.running ?? onboardingRunning + activeTasks

  // ── derived previews ─────────────────────────────────────────────────────────
  const recentAgents = [...agents]
    .sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0))
    .slice(0, 4)
  const usageSorted = [...usage].sort(
    (a, b) => (b.window?.total_tokens || 0) - (a.window?.total_tokens || 0),
  )
  const activity = buildActivity(ctxNodes, 8)
  const ctxRuns = new Set(ctxNodes.map((n) => n.workflow_id || 'unknown')).size

  const policyAgents = policy?.policy?.agents ? Object.keys(policy.policy.agents).length : 0
  const policyTools = policy?.policy?.tools ? Object.keys(policy.policy.tools).length : 0

  const sandbox = health?.sandbox
  const sandboxStatus = sandbox?.status

  // Every dashboard tile, in its default order. `span` is how many of the four
  // columns it occupies. DraggableGrid renders these into one reorderable board
  // (drag to rearrange; the chosen order is remembered per browser).
  const tiles = [
    {
      id: 'agents-running',
      span: 1,
      render: () => (
        <StatCard
          featured
          label="Agents Running"
          value={dash ?? agentCount}
          sub={`${activeAgents} active${agentCount ? ` · ${Math.round((activeAgents / agentCount) * 100) || 0}% of fleet` : ''}`}
          onClick={() => go('radar')}
        />
      ),
    },
    {
      id: 'workflows-running',
      span: 1,
      render: () => (
        <StatCard
          label="Workflows Running"
          value={dash ?? workflowsRunning}
          accent="emerald"
          sub={`${onboardingRunning} onboarding · ${activeTasks} task exec`}
          onClick={() => go('workflow')}
        />
      ),
    },
    {
      id: 'tool-calls',
      span: 1,
      render: () => (
        <StatCard
          label="Tool Calls (live)"
          value={dash ?? activeTasks}
          accent="violet"
          sub="in-flight governed executions"
          onClick={() => go('tokens')}
        />
      ),
    },
    {
      id: 'quarantined',
      span: 1,
      render: () => (
        <StatCard
          label="Quarantined Agents"
          value={dash ?? quarantined}
          accent={quarantined > 0 ? 'rose' : 'slate'}
          sub="held until control hooks observed"
          onClick={() => go('radar')}
        />
      ),
    },
    {
      id: 'governed-activity',
      span: 2,
      render: () => (
        <PreviewCard
          title="Governed Activity"
          subtitle="Recorded governed steps over time"
          onClick={() => go('context')}
        >
          <AreaChart
            formatValue={fmtInt}
            series={[
              { label: 'steps', color: '#3a7d52', values: activity.steps },
              { label: 'runs', color: '#f59e0b', values: activity.runs },
            ]}
          />
          <div className="mt-4 flex items-center gap-4 text-xs text-slate-400">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-[#3a7d52]" />
              <span className="font-bold text-brand-900">{fmtInt(ctxNodes.length)}</span> steps
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-[#f59e0b]" />
              <span className="font-bold text-brand-900">{ctxRuns}</span> runs
            </span>
            <span className="ml-auto inline-flex items-center gap-1 font-medium text-brand-600">
              Open Context Graph
              <Icon name="arrowUpRight" className="h-3.5 w-3.5" strokeWidth={2.2} />
            </span>
          </div>
        </PreviewCard>
      ),
    },
    {
      id: 'approvals',
      span: 1,
      render: () => <ApprovalsCard approvals={approvals} onClick={() => go('approvals')} />,
    },
    {
      id: 'radar',
      span: 1,
      render: () => (
        <PreviewCard
          title="Radar"
          subtitle={`${agentCount} agent${agentCount === 1 ? '' : 's'} on the radar`}
          onClick={() => go('radar')}
        >
          <div className="space-y-2.5">
            {recentAgents.length === 0 ? (
              <Empty>No agents detected yet.</Empty>
            ) : (
              recentAgents.map((a) => (
                <div key={a.id} className="flex items-center gap-2.5">
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${a.alive ? 'bg-brand-500' : 'bg-rose-400'}`}
                  />
                  <span className="flex-1 truncate text-sm font-medium text-brand-900">
                    {a.name}
                  </span>
                  <StatusBadge value={a.autonomy_profile || a.status} />
                </div>
              ))
            )}
          </div>
        </PreviewCard>
      ),
    },
    {
      id: 'token-monitor',
      span: 2,
      render: () => (
        <PreviewCard
          title="Token Monitor"
          subtitle="Budget usage per running agent"
          onClick={() => go('tokens')}
        >
          {usageSorted.length === 0 ? (
            <Empty>No token usage reported yet.</Empty>
          ) : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {usageSorted.map((u) => {
                const pct = pctCapped(u.budget?.ratio || 0)
                return (
                  <Gauge
                    key={u.agent_id}
                    value={pct}
                    max={100}
                    display={`${pct}%`}
                    label={u.name}
                    sub={`${fmtInt(u.window?.total_tokens)} tok`}
                    tone={u.budget?.state || 'ok'}
                  />
                )
              })}
            </div>
          )}
        </PreviewCard>
      ),
    },
    {
      id: 'fleet-health',
      span: 1,
      render: () => (
        <PreviewCard
          title="Fleet Health"
          subtitle="Autonomy distribution"
          onClick={() => go('radar')}
        >
          <div className="flex items-center gap-4">
            <Donut
              segments={[
                { value: activeAgents, color: '#45b06a' },
                { value: restricted, color: '#7fbd93' },
                { value: quarantined, color: '#f43f5e' },
              ]}
              centerTop={agentCount}
              centerBottom="agents"
            />
            <div className="space-y-2 text-xs">
              <Legend color="#45b06a" label="Active" value={activeAgents} />
              <Legend color="#7fbd93" label="Restricted" value={restricted} />
              <Legend color="#f43f5e" label="Quarantined" value={quarantined} />
            </div>
          </div>
        </PreviewCard>
      ),
    },
    {
      id: 'sandbox',
      span: 1,
      render: () => (
        <SandboxCard sandbox={sandbox} status={sandboxStatus} onClick={() => go('sandbox')} />
      ),
    },
    {
      id: 'workflow',
      span: 2,
      render: () => (
        <PreviewCard
          title="Workflow"
          subtitle="Live from Temporal"
          onClick={() => go('workflow')}
        >
          <WorkflowOverview counts={wfCounts} />
        </PreviewCard>
      ),
    },
    {
      id: 'agent-hooks',
      span: 1,
      render: () => (
        <PreviewCard
          title="Agent Hooks"
          subtitle="Lifecycle dispatcher"
          onClick={() => go('hooks')}
        >
          <div className="flex items-center gap-2">
            <Badge tone={hooksStatus.enabled ? 'green' : 'slate'}>
              {hooksStatus.enabled ? 'system on' : 'off'}
            </Badge>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <MiniStat label="Hooks loaded" value={hooksStatus.hook_count ?? '—'} tone="green" />
            <MiniStat
              label="Event types"
              value={Object.keys(hooksStatus.subscriptions || {}).length || '—'}
              tone="slate"
            />
          </div>
        </PreviewCard>
      ),
    },
    {
      id: 'operator-policy',
      span: 1,
      render: () => (
        <PreviewCard
          title="Operator Policy"
          subtitle="Allow / risk rules"
          onClick={() => go('policy')}
        >
          <div className="flex items-center gap-2">
            <Badge tone={policy?.stored ? (policy?.enabled ? 'green' : 'amber') : 'slate'}>
              {policy?.stored ? (policy?.enabled ? 'enforced' : 'stored · off') : 'no policy'}
            </Badge>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <MiniStat label="Agent rules" value={policyAgents || '—'} tone="green" />
            <MiniStat label="Tool rules" value={policyTools || '—'} tone="slate" />
          </div>
        </PreviewCard>
      ),
    },
  ]

  return <DraggableGrid storageKey="awcp-dashboard-order" tiles={tiles} />
}

// ── building blocks ──────────────────────────────────────────────────────────

function PreviewCard({ title, subtitle, onClick, children, className = '' }) {
  return (
    <button
      onClick={onClick}
      className={`group flex h-full w-full flex-col rounded-2xl border border-slate-100 bg-white p-5 text-left shadow-card transition hover:-translate-y-0.5 hover:shadow-card-hover ${className}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-bold tracking-tight text-brand-900">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
        </div>
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-slate-200 text-slate-400 transition group-hover:border-brand-300 group-hover:text-brand-600">
          <Icon name="arrowUpRight" className="h-4 w-4" strokeWidth={2} />
        </span>
      </div>
      <div className="mt-4 flex-1">{children}</div>
    </button>
  )
}

// Temporal run mix as a single proportional status bar + an icon legend —
// modeled on the "Overall Workflow Overview" reference: Done / Running /
// Terminated / Pending, each segment sized by its share of the total.
function WorkflowOverview({ counts }) {
  const done = counts.completed ?? 0
  const running = counts.running ?? 0
  const failed =
    (counts.terminated || 0) +
    (counts.canceled || 0) +
    (counts.failed || 0) +
    (counts.timed_out || 0)
  const known = done + running + failed
  const total = counts.total ?? known
  const pending = Math.max(0, total - known)
  const denom = Math.max(1, total)
  const pct = (v) => Math.round((v / denom) * 100)

  const segs = [
    { key: 'done', label: 'Done', value: done, color: '#3a7d52', icon: 'check' },
    { key: 'running', label: 'Running', value: running, color: '#f59e0b', icon: 'refresh' },
    { key: 'failed', label: 'Terminated', value: failed, color: '#f43f5e', icon: 'close' },
    { key: 'pending', label: 'Pending', value: pending, color: '#94a3b8', icon: 'clock' },
  ]

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          Run overview
        </span>
        <span className="text-xs text-slate-500">
          Total <span className="font-bold text-brand-900">{fmtInt(total)}</span>
        </span>
      </div>

      {/* proportional status bar */}
      <div className="flex h-7 w-full gap-0.5 overflow-hidden rounded-full bg-slate-100 dark:bg-white/10">
        {segs
          .filter((s) => s.value > 0)
          .map((s) => (
            <div
              key={s.key}
              title={`${s.label}: ${fmtInt(s.value)} · ${pct(s.value)}%`}
              style={{ width: `${(s.value / denom) * 100}%`, backgroundColor: s.color }}
              className="flex items-center justify-center transition-all duration-500"
            >
              <span className="px-1 text-[10px] font-bold text-white">{pct(s.value)}%</span>
            </div>
          ))}
      </div>

      {/* icon legend (Done / Running / Terminated / Pending) */}
      <div className="grid grid-cols-2 gap-2">
        {segs.map((s) => (
          <div
            key={s.key}
            className="flex items-center gap-2 rounded-xl bg-slate-50 px-2.5 py-2 dark:bg-white/5"
          >
            <span
              className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-white"
              style={{ backgroundColor: s.color }}
            >
              <Icon name={s.icon} className="h-4 w-4" strokeWidth={2.4} />
            </span>
            <div className="min-w-0">
              <div className="truncate text-[11px] font-semibold text-brand-900">{s.label}</div>
              <div className="text-[10px] tabular-nums text-slate-400">
                {fmtInt(s.value)} · {pct(s.value)}%
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// A green "reminder"-style call-to-action card for the pending approvals queue.
function ApprovalsCard({ approvals, onClick }) {
  const count = approvals.length
  const has = count > 0
  return (
    <button
      onClick={onClick}
      className={`group flex h-full w-full flex-col rounded-2xl p-5 text-left shadow-card transition hover:-translate-y-0.5 hover:shadow-card-hover ${
        has
          ? 'bg-gradient-to-br from-[#45b06a] via-[#348a52] to-[#256b3c] text-white'
          : 'border border-slate-100 bg-white'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className={`text-base font-bold tracking-tight ${has ? 'text-white' : 'text-brand-900'}`}>
            Approvals
          </h3>
          <p className={`mt-0.5 text-xs ${has ? 'text-white/80' : 'text-slate-400'}`}>
            {has ? 'Agents paused on a write action' : 'Write-action queue'}
          </p>
        </div>
        <span
          className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${
            has ? 'bg-white text-brand-700' : 'border border-slate-200 text-slate-400 group-hover:text-brand-600'
          }`}
        >
          <Icon name="arrowUpRight" className="h-4 w-4" strokeWidth={2} />
        </span>
      </div>

      <div className="mt-4 flex-1">
        <div className={`text-4xl font-extrabold leading-none ${has ? 'text-white' : 'text-brand-900'}`}>
          {count}
        </div>
        <div className={`mt-1 text-xs ${has ? 'text-white/80' : 'text-slate-400'}`}>
          {has ? 'awaiting your decision' : 'nothing waiting'}
        </div>

        {has && (
          <div className="mt-3 space-y-1.5">
            {approvals.slice(0, 2).map((r) => (
              <div key={r.id} className="flex items-center gap-2 text-xs text-white/90">
                <span className="truncate font-medium">{r.agent_name || r.agent_id || '—'}</span>
                <span className="truncate font-mono text-white/70">{r.action}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <span
        className={`mt-4 inline-flex items-center gap-1.5 self-start rounded-xl px-3 py-2 text-xs font-semibold ${
          has ? 'bg-white text-brand-700' : 'bg-brand-50 text-brand-700'
        }`}
      >
        Review queue
        <Icon name="arrowUpRight" className="h-3.5 w-3.5" strokeWidth={2.2} />
      </span>
    </button>
  )
}

// A dark "device"-style card for the sandbox, echoing the reference Time Tracker.
function SandboxCard({ sandbox, status, onClick }) {
  const running = status === 'running'
  return (
    <button
      onClick={onClick}
      className="group relative flex h-full w-full flex-col overflow-hidden rounded-2xl bg-gradient-to-br from-[#45b06a] via-[#348a52] to-[#256b3c] p-5 text-left text-white shadow-card transition hover:-translate-y-0.5 hover:shadow-card-hover"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-bold tracking-tight">Sandbox</h3>
          <p className="mt-0.5 text-xs text-white/70">Isolated execution</p>
        </div>
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/15 text-white">
          <Icon name="sandbox" className="h-4 w-4" strokeWidth={1.9} />
        </span>
      </div>
      <div className="mt-5 flex-1">
        <div className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${running ? 'animate-pulse bg-brand-200' : 'bg-white/40'}`} />
          <span className="text-2xl font-extrabold tracking-tight">
            {prettyKind(status || 'unknown')}
          </span>
        </div>
        <div className="mt-2 truncate font-mono text-[11px] text-white/60">
          {sandbox?.image || 'no container image'}
        </div>
        {sandbox?.sandbox_id && (
          <div className="mt-1 font-mono text-[11px] text-white/50">
            id {shortId(sandbox.sandbox_id, 10)}
          </div>
        )}
      </div>
    </button>
  )
}

// SVG donut gauge (reference "Project Progress").
function Donut({ segments, centerTop, centerBottom, size = 120, stroke = 15 }) {
  const r = (size - stroke) / 2
  const C = 2 * Math.PI * r
  const total = segments.reduce((s, x) => s + x.value, 0) || 1
  let acc = 0
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          className="stroke-slate-200 dark:stroke-white/10"
        />
        {segments.map((seg, i) => {
          if (seg.value <= 0) return null
          const len = (seg.value / total) * C
          const el = (
            <circle
              key={i}
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={seg.color}
              strokeWidth={stroke}
              strokeDasharray={`${len} ${C - len}`}
              strokeDashoffset={-acc}
              strokeLinecap="round"
            />
          )
          acc += len
          return el
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-extrabold leading-none text-brand-900">{centerTop}</span>
        <span className="text-[11px] text-slate-400">{centerBottom}</span>
      </div>
    </div>
  )
}

function Legend({ color, label, value }) {
  return (
    <div className="flex items-center gap-2">
      <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
      <span className="text-slate-500">{label}</span>
      <span className="ml-auto font-semibold text-brand-900">{value}</span>
    </div>
  )
}

const MINI_TONE = {
  green: 'text-brand-700',
  amber: 'text-amber-600',
  rose: 'text-rose-600',
  slate: 'text-slate-600',
}

function MiniStat({ label, value, tone = 'slate' }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
      <div className={`text-xl font-extrabold leading-none ${MINI_TONE[tone] || MINI_TONE.slate}`}>
        {value}
      </div>
      <div className="mt-1 text-[11px] text-slate-400">{label}</div>
    </div>
  )
}

function Empty({ children }) {
  return <div className="py-4 text-center text-xs text-slate-400">{children}</div>
}

// Split context nodes into `bins` equal-time buckets across their own span,
// returning, per bucket, the step count (nodes) and the run count (distinct
// workflow ids active in that window). Both series share the same boundaries so
// the two trend lines line up on the x-axis. All-zero when there's no data.
function buildActivity(nodes, bins = 8) {
  const items = nodes
    .filter((n) => n.ts)
    .map((n) => ({ ts: Number(n.ts), run: n.workflow_id || 'unknown' }))
    .sort((a, b) => a.ts - b.ts)
  const steps = Array.from({ length: bins }, () => 0)
  const runSets = Array.from({ length: bins }, () => new Set())
  if (items.length === 0) return { steps, runs: steps.slice() }
  const min = items[0].ts
  const max = items[items.length - 1].ts
  const span = Math.max(1, max - min)
  for (const it of items) {
    let idx = Math.floor(((it.ts - min) / span) * bins)
    if (idx >= bins) idx = bins - 1
    if (idx < 0) idx = 0
    steps[idx]++
    runSets[idx].add(it.run)
  }
  return { steps, runs: runSets.map((s) => s.size) }
}
