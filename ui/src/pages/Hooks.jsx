import { useState, useEffect } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import {
  getHooks,
  getHooksRecent,
  enableHook,
  disableHook,
  getGuard,
  setGuard,
  testGuard,
  getUserAgents,
  getToolPolicy,
  setToolRisk,
} from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { Badge, StatusBadge } from '../components/Badge.jsx'
import { StatCard } from '../components/StatCard.jsx'
import { timeAgo, prettyKind, fmtInt } from '../lib/format.js'

// Fetch the hook registry, the recent-events ring buffer, and the policy-guard
// config together. getHooks() is allowed to throw (a 404 means the agent_hooks
// package isn't mounted) so the page can show a "not mounted" notice; the rest
// are best-effort.
const load = async () => {
  const [hooks, recent, guard, userAgents, toolPolicy] = await Promise.all([
    getHooks(),
    getHooksRecent(60).catch(() => []),
    getGuard().catch(() => null),
    getUserAgents().catch(() => []),
    getToolPolicy().catch(() => null),
  ])
  return { hooks, recent, guard, userAgents, toolPolicy }
}

const CAT_TONE = { observer: 'slate', guard: 'amber' }

// Tier → colour, used by the Tool Risk Policy sliders. Tiers come from the OPA
// agent (env-driven) so this only styles the known names; unknown tiers fall to slate.
const TIER_COLOR = {
  low: 'text-emerald-600',
  medium: 'text-amber-600',
  high: 'text-orange-600',
  severe: 'text-rose-600',
}

export default function Hooks() {
  const { data, error, loading, refresh } = usePoll(load, [])

  // Package not mounted (gateway up, but /hooks 404s) — friendly notice.
  if (error && !data) {
    return (
      <Panel title="Agent Hooks" subtitle="Lifecycle hooks fired by the control plane">
        <div className="px-5 py-10 text-center text-sm text-slate-500">
          The agent-hooks package isn’t mounted on the gateway
          <div className="mt-1 font-mono text-xs text-slate-400">
            (src/awcp/agent_hooks absent or AWCP_HOOKS_ENABLED=false) — {error}
          </div>
        </div>
      </Panel>
    )
  }

  const status = data?.hooks?.status || {}
  const hooks = data?.hooks?.hooks || []
  const recent = data?.recent || []
  const subsCount = Object.keys(status.subscriptions || {}).length
  // Real agents + the real union of their tool catalogs — nothing hardcoded.
  const userAgents = data?.userAgents || []
  const allTools = Array.from(new Set(userAgents.flatMap((a) => a.tools || []))).sort()

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          label="System"
          value={status.enabled ? 'On' : 'Off'}
          accent={status.enabled ? 'emerald' : 'rose'}
          sub="agent-hooks dispatcher"
        />
        <StatCard label="Hooks loaded" value={status.hook_count ?? '—'} accent="indigo" />
        <StatCard label="Event types" value={subsCount || '—'} accent="violet" sub="lifecycle points wired" />
        <StatCard label="Recent events" value={recent.length} accent="slate" sub="in the live buffer" />
      </div>

      {/* ── Policy-guard demo controls (enable + test, no terminal) ──────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        <GuardControl guard={data?.guard} tools={allTools} onDone={refresh} />
        <TestGate agents={userAgents} tools={allTools} onDone={refresh} />
      </div>

      {/* ── Tool Risk Policy: per-tool tier sliders (the OPA agent) ───────── */}
      <ToolRiskPolicy policy={data?.toolPolicy} tools={allTools} onDone={refresh} />

      {/* ── Registered hooks ─────────────────────────────────────────────── */}
      <Panel
        title="Registered hooks"
        subtitle="Each callback, what it subscribes to, how often it has fired, and a live enable/disable toggle"
      >
        <Table
          columns={['Hook', 'Category', 'Priority', 'Subscriptions', 'Calls', 'Errors', 'Denies', '']}
        >
          {loading && !data ? (
            <EmptyRow colSpan={8}>Loading hooks…</EmptyRow>
          ) : hooks.length === 0 ? (
            <EmptyRow colSpan={8}>No hooks registered.</EmptyRow>
          ) : (
            hooks.map((h) => {
              const s = h.stats || {}
              return (
                <tr key={h.name} className="hover:bg-slate-50">
                  <Td>
                    <span className="font-mono text-sm font-medium text-brand-900">{h.name}</span>
                  </Td>
                  <Td>
                    <Badge tone={CAT_TONE[h.category] || 'slate'}>{h.category}</Badge>
                  </Td>
                  <Td className="font-mono text-slate-600">{h.priority}</Td>
                  <Td>
                    <span
                      className="font-mono text-slate-600"
                      title={(h.subscriptions || []).join(', ')}
                    >
                      {(h.subscriptions || []).length}
                    </span>
                  </Td>
                  <Td className="font-mono text-slate-700">{fmtInt(s.calls)}</Td>
                  <Td className={`font-mono ${s.errors ? 'font-semibold text-rose-600' : 'text-slate-400'}`}>
                    {s.errors || 0}
                  </Td>
                  <Td className={`font-mono ${s.denies ? 'font-semibold text-rose-600' : 'text-slate-400'}`}>
                    {s.denies || 0}
                  </Td>
                  <Td>
                    <ToggleButton name={h.name} enabled={h.enabled} onDone={refresh} />
                  </Td>
                </tr>
              )
            })
          )}
        </Table>
      </Panel>

      {/* ── Recent hook events ───────────────────────────────────────────── */}
      <Panel
        title="Recent hook events"
        subtitle="Newest first · the lifecycle stream as agents run · ⛔ marks a guard veto"
      >
        <Table columns={['When', 'Event', 'Agent', 'Decision', 'Hooks fired']}>
          {recent.length === 0 ? (
            <EmptyRow colSpan={5}>
              No hook events yet — send a task in the chat UI or gate an action.
            </EmptyRow>
          ) : (
            recent.map((e, i) => {
              const fired = (e.hooks || []).map((x) => x.hook).join(', ')
              const deny = e.decision === 'deny'
              const reason = (e.hooks || []).find((x) => x.reason)?.reason
              return (
                <tr key={`${e.ts}-${i}`} className={deny ? 'bg-rose-50/50' : 'hover:bg-slate-50'}>
                  <Td className="whitespace-nowrap text-xs text-slate-500">{timeAgo(e.ts)}</Td>
                  <Td>
                    <span className="font-mono text-xs font-medium text-brand-900">
                      {prettyKind(e.type)}
                    </span>
                    {e.guard_point && (
                      <span
                        className="ml-1.5 text-[10px] font-semibold uppercase tracking-wide text-amber-600"
                        title="a guard hook can veto at this point"
                      >
                        guard
                      </span>
                    )}
                  </Td>
                  <Td className="font-mono text-xs text-slate-500">{e.agent_id || '—'}</Td>
                  <Td>
                    {deny ? (
                      <Badge tone="red" title={reason}>
                        ⛔ deny
                      </Badge>
                    ) : (
                      <StatusBadge value="allow" />
                    )}
                  </Td>
                  <Td className="text-xs text-slate-500">{fired || '—'}</Td>
                </tr>
              )
            })
          )}
        </Table>
      </Panel>
    </div>
  )
}

// Live enable/disable toggle — POSTs to /hooks/{name}/{enable|disable}.
function ToggleButton({ name, enabled, onDone }) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      disabled={busy}
      onClick={async () => {
        setBusy(true)
        try {
          await (enabled ? disableHook(name) : enableHook(name))
          await onDone?.()
        } catch (e) {
          // surface failures without crashing the table
          // eslint-disable-next-line no-alert
          alert(`Toggle failed: ${e.message || e}`)
        } finally {
          setBusy(false)
        }
      }}
      className={`whitespace-nowrap rounded-md border px-2.5 py-1 text-xs font-medium transition disabled:opacity-50 ${
        enabled
          ? 'border-brand-300 bg-brand-50 text-brand-700 hover:border-brand-500'
          : 'border-slate-300 bg-white text-slate-500 hover:border-brand-500 hover:text-brand-700'
      }`}
      title={enabled ? 'Disable this hook (keeps it registered)' : 'Enable this hook'}
    >
      {busy ? '…' : enabled ? 'enabled' : 'disabled'}
    </button>
  )
}

// Enable/configure the policy-guard at runtime (POST /hooks/guard) — no restart.
// The deny-list is built from the project's REAL tool catalog (click to add).
function GuardControl({ guard, tools = [], onDone }) {
  const enabled = !!guard?.enabled
  const [list, setList] = useState('')
  const [busy, setBusy] = useState(false)

  // Keep the field in sync with the server's deny-list whenever the guard is on.
  useEffect(() => {
    if (guard?.deny_tools?.length) setList(guard.deny_tools.join(', '))
  }, [guard?.deny_tools?.join(',')])

  const current = list.split(',').map((s) => s.trim()).filter(Boolean)
  const toggleTool = (t) => {
    const set = new Set(current)
    set.has(t) ? set.delete(t) : set.add(t)
    setList(Array.from(set).join(', '))
  }

  const apply = async (on) => {
    setBusy(true)
    try {
      await setGuard(on ? current : [], on)
      await onDone?.()
    } catch (e) {
      // eslint-disable-next-line no-alert
      alert(`Guard update failed: ${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Policy Guard"
      subtitle="A guard hook that vetoes deny-listed tools at the gate — toggle it live"
    >
      <div className="space-y-3 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-500">Status:</span>
          {enabled ? <Badge tone="green">enabled</Badge> : <Badge tone="slate">off</Badge>}
          {enabled && guard?.deny_tools?.length > 0 && (
            <span className="text-xs text-slate-500">
              blocking <span className="font-mono text-slate-700">{guard.deny_tools.join(', ')}</span>
            </span>
          )}
        </div>
        <div>
          <label className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Deny-list
          </label>
          {tools.length > 0 ? (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {tools.map((t) => {
                const on = current.includes(t)
                return (
                  <button
                    key={t}
                    onClick={() => toggleTool(t)}
                    disabled={busy}
                    className={`rounded-md border px-2 py-0.5 font-mono text-[11px] transition disabled:opacity-50 ${
                      on
                        ? 'border-rose-300 bg-rose-50 text-rose-700'
                        : 'border-slate-300 bg-white text-slate-500 hover:border-brand-400 hover:text-brand-700'
                    }`}
                    title={on ? 'click to remove from deny-list' : 'click to add to deny-list'}
                  >
                    {on ? '⛔ ' : ''}
                    {t}
                  </button>
                )
              })}
            </div>
          ) : (
            <p className="mt-1 text-xs text-slate-400">
              No tools discovered yet — start an agent so its tool catalog appears here.
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => apply(true)}
            disabled={busy || current.length === 0}
            className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
            title={current.length === 0 ? 'pick at least one tool above' : ''}
          >
            {busy ? '…' : enabled ? 'Update guard' : 'Enable guard'}
          </button>
          <button
            onClick={() => apply(false)}
            disabled={busy || !enabled}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-rose-400 hover:text-rose-600 disabled:opacity-50"
          >
            Disable
          </button>
        </div>
      </div>
    </Panel>
  )
}

// One-click veto test (POST /hooks/guard/test) — fires a gate evaluation through
// the guard and shows the decision. Driven by REAL agents + REAL tools; nothing
// hardcoded. Deterministic (isolates the guard from agent budget/quarantine).
function TestGate({ agents = [], tools = [], onDone }) {
  const opts = agents.map((a) => ({
    value: a.agent_id || a.id,
    label: `${a.id}${a.running ? '' : ' (stopped)'}`,
  }))
  const [agent, setAgent] = useState('')
  const [action, setAction] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)

  // Default the selects to the first real agent / tool once the data arrives.
  useEffect(() => {
    if (!agent && opts.length) setAgent(opts[0].value)
  }, [opts.map((o) => o.value).join(',')])
  useEffect(() => {
    if (!action && tools.length) setAction(tools.includes('external_post') ? 'external_post' : tools[0])
  }, [tools.join(',')])

  const run = async () => {
    if (!agent || !action) return
    setBusy(true)
    try {
      const r = await testGuard(agent, action)
      setRes(r)
      await onDone?.()
    } catch (e) {
      setRes({ error: e.message || String(e) })
    } finally {
      setBusy(false)
    }
  }
  const deny = res?.decision === 'deny'
  const hasData = opts.length > 0 && tools.length > 0

  return (
    <Panel
      title="Test the gate"
      subtitle="Fire a gate check (real agent + real tool) through the guard — allow vs ⛔ deny"
    >
      <div className="space-y-3 px-5 py-4">
        {!hasData ? (
          <p className="text-sm text-slate-400">
            No running agent with a tool catalog yet — start an agent (e.g. from the chat UI) and its
            agents + tools will populate here.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-end gap-2">
              <div className="flex-1">
                <label className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Agent</label>
                <select
                  value={agent}
                  onChange={(e) => setAgent(e.target.value)}
                  disabled={busy}
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-50"
                >
                  {opts.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex-1">
                <label className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Tool</label>
                <select
                  value={action}
                  onChange={(e) => setAction(e.target.value)}
                  disabled={busy}
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 font-mono text-xs text-slate-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-50"
                >
                  {tools.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
              <button
                onClick={run}
                disabled={busy}
                className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
              >
                {busy ? 'running…' : 'Run gate check'}
              </button>
            </div>
            {res &&
              (res.error ? (
                <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {res.error}
                </div>
              ) : (
                <div
                  className={`rounded-md border px-3 py-2 text-sm ${
                    deny ? 'border-rose-200 bg-rose-50' : 'border-brand-200 bg-brand-50'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {deny ? <Badge tone="red">⛔ deny</Badge> : <Badge tone="green">allow</Badge>}
                    <span className="font-mono text-xs text-slate-500">mode={res.mode}</span>
                  </div>
                  {res.reason && <div className="mt-1 text-xs text-slate-600">{res.reason}</div>}
                </div>
              ))}
            <p className="text-xs text-slate-400">
              Add a tool to the deny-list on the left, then run it here → ⛔ deny. A tool that isn’t
              deny-listed → allow.
            </p>
          </>
        )}
      </div>
    </Panel>
  )
}

// ── Tool Risk Policy: a per-tool tier slider, enforced by the OPA agent ────────
// Every tool the real agents expose gets a slider across the OPA agent's tier
// vocabulary (low → severe, env-driven). Tiers in the block set (high/severe)
// block the answer in the user UI. Reads /tools/policy, writes /tools/policy/{name}.
function ToolRiskPolicy({ policy, tools = [], onDone }) {
  const enabled = !!policy?.enabled
  const tiers = policy?.tiers || []
  const blockTiers = policy?.block_tiers || []
  const defaultTier = policy?.default_tier || tiers[0] || 'low'
  const map = policy?.policy || {}

  return (
    <Panel
      title="Tool Risk Policy"
      subtitle="Set each tool's risk tier — high/severe tools are blocked by the OPA agent and the answer is stopped in the user UI"
      right={
        enabled ? (
          <span className="text-xs text-slate-500">
            blocks <span className="font-mono text-rose-600">{blockTiers.join(', ') || '—'}</span>
          </span>
        ) : (
          <Badge tone="slate">OPA agent off</Badge>
        )
      }
    >
      <div className="space-y-3 px-5 py-4">
        {!enabled ? (
          <p className="text-sm text-slate-400">
            The OPA agent isn’t wired to the gateway — start it and set{' '}
            <span className="font-mono text-xs">AWCP_OPA_AGENT_URL</span> so per-tool tiers can be
            enforced.
          </p>
        ) : tools.length === 0 ? (
          <p className="text-sm text-slate-400">
            No tools discovered yet — start an agent so its tool catalog appears here.
          </p>
        ) : (
          <div className="space-y-3">
            {tools.map((t) => (
              <ToolRiskSlider
                key={t}
                tool={t}
                tiers={tiers}
                blockTiers={blockTiers}
                current={map[t] || defaultTier}
                onCommit={async (tier) => {
                  await setToolRisk(t, tier)
                  await onDone?.()
                }}
              />
            ))}
          </div>
        )}
      </div>
    </Panel>
  )
}

// One tool's tier slider: a range input across the tier vocabulary. Commits on
// release (POST /tools/policy/{name}); the label colours by tier and flags blocks.
function ToolRiskSlider({ tool, tiers, blockTiers, current, onCommit }) {
  const idxOf = (tier) => Math.max(0, tiers.indexOf(tier))
  const [idx, setIdx] = useState(idxOf(current))
  const [busy, setBusy] = useState(false)

  // Re-sync when the server's value changes (e.g. after a refresh).
  useEffect(() => {
    setIdx(idxOf(current))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, tiers.join(',')])

  const tier = tiers[idx] || current
  const blocks = blockTiers.includes(tier)

  const commit = async () => {
    if (busy) return
    setBusy(true)
    try {
      await onCommit(tier)
    } catch (e) {
      // eslint-disable-next-line no-alert
      alert(`Set tier failed: ${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-3">
      <span className="w-40 shrink-0 truncate font-mono text-xs text-slate-700" title={tool}>
        {tool}
      </span>
      <input
        type="range"
        min={0}
        max={Math.max(0, tiers.length - 1)}
        step={1}
        value={idx}
        disabled={busy}
        onChange={(e) => setIdx(Number(e.target.value))}
        onMouseUp={commit}
        onTouchEnd={commit}
        onKeyUp={commit}
        className="h-1.5 flex-1 cursor-pointer accent-brand-600 disabled:opacity-50"
        title={`${tool}: ${tier}`}
      />
      <span className={`w-24 shrink-0 text-right text-xs font-semibold ${TIER_COLOR[tier] || 'text-slate-600'}`}>
        {tier}
        {blocks && <span className="ml-1 text-[10px] uppercase tracking-wide text-rose-500">⛔</span>}
      </span>
    </div>
  )
}
