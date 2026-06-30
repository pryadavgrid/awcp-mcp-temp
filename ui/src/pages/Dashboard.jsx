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
  const wfRows = wf?.workflows || []
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
  const topUsage = [...usage]
    .sort((a, b) => (b.window?.total_tokens || 0) - (a.window?.total_tokens || 0))
    .slice(0, 3)
  const activityBuckets = buildBuckets(ctxNodes.map((n) => n.ts), 8)
  const ctxRuns = new Set(ctxNodes.map((n) => n.workflow_id || 'unknown')).size
  const recentWf = [...wfRows].slice(0, 3)

  const policyAgents = policy?.policy?.agents ? Object.keys(policy.policy.agents).length : 0
  const policyTools = policy?.policy?.tools ? Object.keys(policy.policy.tools).length : 0

  const sandbox = health?.sandbox
  const sandboxStatus = sandbox?.status

  return (
    <div className="space-y-5">
      {/* ── Row 1 · headline stats (click to drill in) ───────────────────────── */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          featured
          label="Agents Running"
          value={dash ?? agentCount}
          sub={`${activeAgents} active${agentCount ? ` · ${Math.round((activeAgents / agentCount) * 100) || 0}% of fleet` : ''}`}
          onClick={() => go('radar')}
        />
        <StatCard
          label="Workflows Running"
          value={dash ?? workflowsRunning}
          accent="emerald"
          sub={`${onboardingRunning} onboarding · ${activeTasks} task exec`}
          onClick={() => go('workflow')}
        />
        <StatCard
          label="Tool Calls (live)"
          value={dash ?? activeTasks}
          accent="violet"
          sub="in-flight governed executions"
          onClick={() => go('tokens')}
        />
        <StatCard
          label="Quarantined Agents"
          value={dash ?? quarantined}
          accent={quarantined > 0 ? 'rose' : 'slate'}
          sub="held until control hooks observed"
          onClick={() => go('radar')}
        />
      </div>

      {/* ── Row 2 · activity (wide) · approvals CTA · radar list ──────────────── */}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4">
        <PreviewCard
          title="Governed Activity"
          subtitle="Recorded governed steps over time"
          onClick={() => go('context')}
          className="md:col-span-2 xl:col-span-2"
        >
          <MiniBars counts={activityBuckets} />
          <div className="mt-4 flex items-center gap-4 text-xs text-slate-400">
            <span>
              <span className="font-bold text-brand-900">{fmtInt(ctxNodes.length)}</span> steps
            </span>
            <span>
              <span className="font-bold text-brand-900">{ctxRuns}</span> runs
            </span>
            <span className="ml-auto inline-flex items-center gap-1 font-medium text-brand-600">
              Open Context Graph
              <Icon name="arrowUpRight" className="h-3.5 w-3.5" strokeWidth={2.2} />
            </span>
          </div>
        </PreviewCard>

        <ApprovalsCard approvals={approvals} onClick={() => go('approvals')} />

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
      </div>

      {/* ── Row 3 · token usage (wide) · fleet donut · sandbox (dark) ─────────── */}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4">
        <PreviewCard
          title="Token Monitor"
          subtitle="Top agents by window usage"
          onClick={() => go('tokens')}
          className="md:col-span-2 xl:col-span-2"
        >
          <div className="space-y-3.5">
            {topUsage.length === 0 ? (
              <Empty>No token usage reported yet.</Empty>
            ) : (
              topUsage.map((u) => {
                const pct = pctCapped(u.budget?.ratio || 0)
                const state = u.budget?.state || 'ok'
                return (
                  <div key={u.agent_id}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="truncate font-medium text-brand-900">{u.name}</span>
                      <span className="text-slate-400">
                        {fmtInt(u.window?.total_tokens)} tok · {pct}%
                      </span>
                    </div>
                    <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                      <div
                        className={`h-full rounded-full ${BAR[state] || BAR.ok}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </PreviewCard>

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

        <SandboxCard sandbox={sandbox} status={sandboxStatus} onClick={() => go('sandbox')} />
      </div>

      {/* ── Row 4 · workflows · agent hooks · operator policy ─────────────────── */}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4">
        <PreviewCard
          title="Workflow"
          subtitle="Live from Temporal"
          onClick={() => go('workflow')}
          className="md:col-span-2 xl:col-span-2"
        >
          <div className="mb-3 flex gap-2">
            <MiniStat label="Running" value={wfCounts.running ?? 0} tone="amber" />
            <MiniStat label="Completed" value={wfCounts.completed ?? 0} tone="green" />
            <MiniStat
              label="Failed"
              value={(wfCounts.failed || 0) + (wfCounts.timed_out || 0)}
              tone="rose"
            />
          </div>
          <div className="space-y-2">
            {recentWf.length === 0 ? (
              <Empty>No workflows yet.</Empty>
            ) : (
              recentWf.map((w) => (
                <div key={w.workflow_id} className="flex items-center gap-2 text-xs">
                  <span className="flex-1 truncate font-mono text-slate-600">{w.workflow_id}</span>
                  <StatusBadge value={w.status} />
                </div>
              ))
            )}
          </div>
        </PreviewCard>

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
      </div>
    </div>
  )
}

const BAR = {
  exhausted: 'bg-rose-500',
  warn: 'bg-amber-500',
  ok: 'bg-brand-500',
}

// ── building blocks ──────────────────────────────────────────────────────────

function PreviewCard({ title, subtitle, onClick, children, className = '' }) {
  return (
    <button
      onClick={onClick}
      className={`group flex flex-col rounded-2xl border border-slate-100 bg-white p-5 text-left shadow-card transition hover:-translate-y-0.5 hover:shadow-card-hover ${className}`}
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

// A green "reminder"-style call-to-action card for the pending approvals queue.
function ApprovalsCard({ approvals, onClick }) {
  const count = approvals.length
  const has = count > 0
  return (
    <button
      onClick={onClick}
      className={`group flex flex-col rounded-2xl p-5 text-left shadow-card transition hover:-translate-y-0.5 hover:shadow-card-hover ${
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
      className="group relative flex flex-col overflow-hidden rounded-2xl bg-gradient-to-br from-[#45b06a] via-[#348a52] to-[#256b3c] p-5 text-left text-white shadow-card transition hover:-translate-y-0.5 hover:shadow-card-hover"
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

// Pill-shaped bar chart (reference "Project Analytics"), tallest bar highlighted.
function MiniBars({ counts }) {
  const max = Math.max(1, ...counts)
  const peak = counts.indexOf(Math.max(...counts))
  return (
    <div className="flex h-32 items-end gap-2.5">
      {counts.map((c, i) => {
        const h = Math.max(10, Math.round((c / max) * 100))
        const isPeak = c > 0 && i === peak
        return (
          <div key={i} className="relative flex flex-1 items-end">
            {isPeak && (
              <span className="absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-white px-2 py-0.5 text-[10px] font-bold text-brand-700 shadow-card ring-1 ring-slate-100">
                {c}
              </span>
            )}
            <div
              className={`w-full rounded-full transition-all ${
                c > 0 ? (isPeak ? 'bg-brand-700' : 'bg-brand-400') : 'bg-slate-100'
              }`}
              style={{ height: `${h}%` }}
            />
          </div>
        )
      })}
    </div>
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

// Split a list of unix-second timestamps into `bins` equal-time buckets across
// their own span, returning per-bucket counts (all-zero when there's no data).
function buildBuckets(tsList, bins = 8) {
  const ts = tsList.filter(Boolean).map(Number).sort((a, b) => a - b)
  const counts = Array.from({ length: bins }, () => 0)
  if (ts.length === 0) return counts
  const min = ts[0]
  const max = ts[ts.length - 1]
  const span = Math.max(1, max - min)
  for (const t of ts) {
    let idx = Math.floor(((t - min) / span) * bins)
    if (idx >= bins) idx = bins - 1
    if (idx < 0) idx = 0
    counts[idx]++
  }
  return counts
}
