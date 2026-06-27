import { useState } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import { getContextFeed, getChainVerify } from '../api.js'
import { Panel } from '../components/Table.jsx'
import { Badge, StatusBadge } from '../components/Badge.jsx'
import { timeAgo } from '../lib/format.js'
import Neo4jGraph from '../components/Neo4jGraph.jsx'

// The context graph = every governed step an agent took, recorded as a node in
// evidence.ledger (event_type='checkpoint') and chained prev_hash → row_hash. We
// poll the global feed once and derive the per-run chains client-side, so the
// view is live and needs a single request.

function stepTone(step) {
  const s = String(step || '').toLowerCase()
  if (s.startsWith('tool:')) return 'blue'
  if (s.startsWith('generate')) return 'green'
  if (s.startsWith('route') || s.startsWith('delegate')) return 'violet'
  return 'slate'
}

// Group the flat node feed into runs (one per workflow_id), each sorted oldest→newest.
function groupRuns(nodes) {
  const byWf = new Map()
  for (const n of nodes) {
    const wf = n.workflow_id || 'unknown'
    if (!byWf.has(wf)) byWf.set(wf, [])
    byWf.get(wf).push(n)
  }
  const runs = []
  for (const [wf, ns] of byWf.entries()) {
    ns.sort((a, b) => (a.ts || 0) - (b.ts || 0))
    runs.push({
      workflow_id: wf,
      agent: ns[ns.length - 1].agent_id || '—',
      steps: ns.length,
      nodes: ns,
      lastTs: Math.max(...ns.map((n) => n.ts || 0)),
    })
  }
  return runs.sort((a, b) => b.lastTs - a.lastTs)
}

export default function ContextGraph() {
  const { data, loading, error } = usePoll(() => getContextFeed(300), [])
  const { data: chain } = usePoll(getChainVerify, [])
  const [selected, setSelected] = useState(null)
  const [view, setView] = useState('timeline') // 'timeline' | 'graph'

  const nodes = (data && data.nodes) || []
  const runs = groupRuns(nodes)
  const totalSteps = nodes.length

  // Resolve the active run: explicit selection if it still exists, else newest.
  const run = runs.find((r) => r.workflow_id === selected) || runs[0] || null

  // The endpoint 404s into an error only if the package isn't mounted; a missing
  // graph just yields an empty feed, so distinguish the two for the user.
  const notMounted = error && /not found|404/i.test(String(error))

  return (
    <div className="space-y-4">
      {/* ── summary chips ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Runs" value={runs.length} />
        <Stat label="Recorded steps" value={totalSteps} />
        <Stat
          label="Last activity"
          value={runs.length ? timeAgo(runs[0].lastTs) : '—'}
        />
        <ChainStat chain={chain} />
      </div>

      {/* ── view toggle: timeline (Postgres ledger) vs graph (Neo4j) ──── */}
      <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 text-sm shadow-sm w-max">
        {[
          ['timeline', '☰ Timeline'],
          ['graph', '◈ Graph'],
        ].map(([id, label]) => (
          <button
            key={id}
            onClick={() => setView(id)}
            className={`rounded-md px-3 py-1.5 transition ${
              view === id ? 'bg-brand-600 font-semibold text-white' : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {view === 'graph' && (
        <Panel
          title="Graph (Neo4j projection)"
          subtitle="Agent → Step ← Workflow, with NEXT lineage — mirrored from the evidence ledger"
        >
          <div className="px-5 py-4">
            <Neo4jGraph />
          </div>
        </Panel>
      )}

      {view === 'timeline' && (
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[20rem_1fr]">
        {/* ── runs list ───────────────────────────────────────────────── */}
        <Panel
          title="Runs"
          subtitle="One workflow per task — newest first"
          right={<span className="text-xs text-slate-500">{runs.length}</span>}
        >
          <div className="max-h-[34rem] divide-y divide-slate-100 overflow-y-auto">
            {loading && !data ? (
              <p className="px-5 py-8 text-center text-sm text-slate-400">Loading…</p>
            ) : runs.length === 0 ? (
              <p className="px-5 py-8 text-center text-sm text-slate-400">
                {notMounted
                  ? 'Context graph endpoint not mounted.'
                  : 'No governed steps recorded yet. Run an agent that calls a tool.'}
              </p>
            ) : (
              runs.map((r) => {
                const isActive = run && r.workflow_id === run.workflow_id
                return (
                  <button
                    key={r.workflow_id}
                    onClick={() => setSelected(r.workflow_id)}
                    className={`flex w-full flex-col items-start gap-1 px-5 py-3 text-left transition ${
                      isActive ? 'bg-brand-50' : 'hover:bg-slate-50'
                    }`}
                  >
                    <span className="font-mono text-xs font-semibold text-brand-900 break-all">
                      {r.workflow_id}
                    </span>
                    <span className="flex items-center gap-2 text-[11px] text-slate-500">
                      <span>{r.agent}</span>
                      <span>·</span>
                      <span>
                        {r.steps} step{r.steps === 1 ? '' : 's'}
                      </span>
                      <span>·</span>
                      <span>{timeAgo(r.lastTs)}</span>
                    </span>
                  </button>
                )
              })
            )}
          </div>
        </Panel>

        {/* ── selected run's chain ────────────────────────────────────── */}
        <Panel
          title={run ? 'Step chain' : 'Context graph'}
          subtitle={
            run
              ? `${run.steps} governed step${run.steps === 1 ? '' : 's'} · tamper-chained prev → row hash`
              : 'Select a run to see its governed-step trail'
          }
          right={
            run ? (
              <span className="font-mono text-[11px] text-slate-400 break-all">
                {run.workflow_id}
              </span>
            ) : null
          }
        >
          {!run ? (
            <p className="px-5 py-12 text-center text-sm text-slate-400">
              Nothing selected.
            </p>
          ) : (
            <div className="px-5 py-5">
              <Timeline nodes={run.nodes} />
            </div>
          )}
        </Panel>
      </div>
      )}
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className="mt-0.5 text-xl font-bold text-brand-900">{value}</div>
    </div>
  )
}

// Tamper-chain integrity from GET /context-graph/verify (re-hashes the ledger).
function ChainStat({ chain }) {
  let value = '…'
  let sub = 'verifying'
  let color = 'text-slate-400'
  if (chain) {
    if (!chain.enabled) {
      value = 'in-memory'; sub = 'Postgres off — not durable'; color = 'text-slate-500'
    } else if (chain.intact) {
      value = '✓ intact'; color = 'text-brand-700'
      sub = `${chain.content_verified}/${chain.total} re-hashed`
    } else {
      value = `⚠ ${chain.breaks?.length || 0} break${(chain.breaks?.length || 0) === 1 ? '' : 's'}`
      color = 'text-rose-700'
      sub = `${chain.total} rows checked`
    }
  }
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[11px] uppercase tracking-wider text-slate-400">Chain integrity</div>
      <div className={`mt-0.5 text-xl font-bold ${color}`}>{value}</div>
      <div className="text-[11px] text-slate-400">{sub}</div>
    </div>
  )
}

// One labelled hash line — full value, monospace, wraps, click-to-select to copy.
function HashRow({ label, value, prefix, tone = 'text-slate-600' }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
      <span className="w-24 shrink-0 text-slate-400">
        {prefix ? `${prefix} ` : ''}
        {label}
      </span>
      <span className={`select-all break-all font-mono ${tone}`}>{value}</span>
    </div>
  )
}

function Timeline({ nodes }) {
  return (
    <ol className="relative">
      {nodes.map((n, i) => {
        const last = i === nodes.length - 1
        const p = n.payload || {}
        const blocked = p.outcome === 'blocked' || p.decision === 'deny'
        return (
          <li key={n.row_hash || i} className="relative pl-9 pb-5 last:pb-0">
            {/* connector line down to the next node */}
            {!last && (
              <span className="absolute left-[13px] top-4 h-full w-px bg-slate-200" />
            )}
            {/* numbered node dot — red for a blocked attempt */}
            <span
              className={`absolute left-1 top-0.5 grid h-6 w-6 place-items-center rounded-full text-[11px] font-bold text-white ring-4 ${
                blocked ? 'bg-rose-500 ring-rose-50' : 'bg-brand-500 ring-brand-50'
              }`}
            >
              {i + 1}
            </span>

            <div
              className={`rounded-lg border bg-white p-3 shadow-sm ${
                blocked ? 'border-rose-200' : 'border-slate-200'
              }`}
            >
              {/* header: step + time */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Badge tone={stepTone(n.step)}>{n.step || 'step'}</Badge>
                <span className="text-[11px] text-slate-400">{timeAgo(n.ts)}</span>
              </div>

              {/* who + governance badges */}
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="text-slate-500">by</span>
                <span className="font-mono text-[11px] text-slate-700">{n.agent_id || '—'}</span>
                {blocked && <Badge tone="red" title="gate denied this step">blocked</Badge>}
                {p.decision && <StatusBadge value={p.decision} />}
                {p.risk && <StatusBadge value={p.risk} title="risk tier" />}
                {p.mode && p.mode !== p.decision && (
                  <Badge tone="slate" title="gate mode">{p.mode}</Badge>
                )}
                {p.tool && (
                  <Badge tone="slate" title="tool">⚒ {p.tool}</Badge>
                )}
              </div>

              {/* denial reason, when blocked */}
              {blocked && p.reason && (
                <div className="mt-2 text-[11px] text-rose-700">{p.reason}</div>
              )}

              {/* resume pointer */}
              {n.resume_pointer && (
                <div className="mt-2 text-[11px] text-slate-500">
                  <span className="text-slate-400">resumes →</span>{' '}
                  <span className="font-mono text-slate-700">{n.resume_pointer}</span>
                </div>
              )}

              {/* tamper chain — full hashes, labelled and click-to-select */}
              <div className="mt-2 space-y-1 border-t border-slate-100 pt-2 text-[11px]">
                {n.context_hash && (
                  <HashRow label="Content hash" value={n.context_hash} tone="text-brand-700" />
                )}
                <HashRow
                  label="Row hash"
                  value={n.row_hash}
                  prefix={n.prev_hash ? '🔗' : '⛓'}
                />
                <HashRow label="Prev hash" value={n.prev_hash || '(genesis)'} />
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
