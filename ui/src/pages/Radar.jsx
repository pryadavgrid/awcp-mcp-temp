import { useEffect, useState } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import { getAgents, getAgentBrief, getToolTiers, setBlockThreshold } from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { StatusBadge } from '../components/Badge.jsx'
import { timeAgo } from '../lib/format.js'

// Tier → colour. Known names are styled directly; any other (env-driven) vocabulary
// falls back to a position-based ramp (first = safe/green … last = severe/red) so the
// bars work for ANY tier list without hardcoding the names.
const TIER_STYLE = {
  low: { text: 'text-brand-600', fill: 'bg-brand-500' },
  medium: { text: 'text-amber-600', fill: 'bg-amber-500' },
  high: { text: 'text-orange-600', fill: 'bg-orange-500' },
  severe: { text: 'text-rose-600', fill: 'bg-rose-500' },
}
const RAMP = [
  { text: 'text-brand-600', fill: 'bg-brand-500' },
  { text: 'text-amber-600', fill: 'bg-amber-500' },
  { text: 'text-orange-600', fill: 'bg-orange-500' },
  { text: 'text-rose-600', fill: 'bg-rose-500' },
]
// Tier → solid fill colour (hex), mirroring TIER_STYLE's fills, for things that need
// a real colour value rather than a Tailwind class (the slider thumb's inline var).
// low=green, medium=yellow, high=orange, severe=red. Unknown tiers fall back to slate.
const TIER_HEX = {
  low: '#22c55e',
  medium: '#f59e0b',
  high: '#f97316',
  severe: '#f43f5e',
}
const tierHex = (tier) => TIER_HEX[tier] || '#64748b'

function tierStyle(tier, tiers) {
  if (TIER_STYLE[tier]) return TIER_STYLE[tier]
  const i = Math.max(0, tiers.indexOf(tier))
  const span = Math.max(1, tiers.length - 1)
  return RAMP[Math.min(RAMP.length - 1, Math.round((i / span) * (RAMP.length - 1)))]
}

// A segmented level-meter bar across the tier vocabulary: segments up to and
// including the call's tier are filled in that tier's colour; the rest stay faint.
function TierBar({ tier, tiers }) {
  const active = Math.max(0, tiers.indexOf(tier))
  const style = tierStyle(tier, tiers)
  return (
    <div className="flex items-center gap-1" title={`${tier} (${active + 1}/${tiers.length})`}>
      {tiers.map((t, i) => (
        <span
          key={t}
          className={`h-2 w-7 rounded-sm transition-colors ${i <= active ? style.fill : 'bg-slate-200'}`}
        />
      ))}
    </div>
  )
}

// Compact skills cell — the AgentCard's denormalized skill ids as small chips,
// capped so a skill-heavy agent doesn't blow out the row. '—' when no card/skills.
function SkillCell({ skills }) {
  if (!skills || skills.length === 0) return <span className="text-slate-400">—</span>
  const shown = skills.slice(0, 3)
  const extra = skills.length - shown.length
  return (
    <div className="flex flex-wrap items-center gap-1" title={skills.join(', ')}>
      {shown.map((s) => (
        <span key={s} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">
          {s}
        </span>
      ))}
      {extra > 0 && <span className="text-[10px] text-slate-400">+{extra}</span>}
    </div>
  )
}

export default function Radar() {
  const { data, loading } = usePoll(getAgents, [])
  const agents = data || []
  const { data: tierData, refresh: refreshTiers } = usePoll(getToolTiers, [])
  // The agent whose live brief modal is open (clicking its "card" chip).
  const [sel, setSel] = useState(null)

  return (
    <div className="space-y-6">
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
          columns={['Name', 'Kind', 'Framework', 'Skills', 'Status', 'Autonomy', 'Onboarding', 'Owner', 'Live']}
        >
          {loading && !data ? (
            <EmptyRow colSpan={9}>Loading agents…</EmptyRow>
          ) : agents.length === 0 ? (
            <EmptyRow colSpan={9}>No agents detected yet.</EmptyRow>
          ) : (
            agents.map((a) => (
              <tr
                key={a.id}
                // stopped agents stay on the radar — flag the whole row in a
                // very light red so they read as "stopped, not gone" (with a
                // dark-mode tint so the flag stays visible there too)
                className={
                  a.alive
                    ? 'hover:bg-slate-50'
                    : 'bg-rose-50 hover:bg-rose-100 dark:bg-rose-500/15 dark:hover:bg-rose-500/25'
                }
              >
                <Td>
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium text-brand-900">{a.name}</span>
                    {a.card_summary && (
                      <button
                        type="button"
                        onClick={() => setSel(a)}
                        className="cursor-pointer rounded bg-brand-100 px-1.5 py-0.5 text-[10px] font-medium text-brand-700 ring-1 ring-inset ring-brand-600/20 transition hover:bg-brand-200"
                        title="Click for a live brief of what this agent is doing"
                      >
                        card{a.card_summary.source === 'synthesized' ? '*' : ''}
                      </button>
                    )}
                  </div>
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
                  <SkillCell skills={(a.card_summary && a.card_summary.skills) || a.skills || []} />
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
                      className={`h-2 w-2 rounded-full ${a.alive ? 'bg-brand-500' : 'bg-rose-500'}`}
                    />
                    <span
                      className={
                        a.alive
                          ? 'text-brand-600 dark:text-brand-300'
                          : 'text-rose-600 dark:text-rose-300'
                      }
                    >
                      {a.alive ? 'live' : 'stop'}
                    </span>
                    <span className="text-xs text-slate-400">· {timeAgo(a.last_seen)}</span>
                  </span>
                </Td>
              </tr>
            ))
          )}
        </Table>
      </Panel>

      <ToolTiers tierData={tierData} onRefresh={refreshTiers} />

      <AgentBriefModal sel={sel} onClose={() => setSel(null)} />
    </div>
  )
}

// Click a "card" chip → a small modal with a LIVE, server-generated brief of what
// the agent is and is doing. It refetches every few seconds while open (and the
// server regenerates it from current state), so it's dynamic, not a static blurb.
function AgentBriefModal({ sel, onClose }) {
  const [state, setState] = useState({ loading: true })
  useEffect(() => {
    if (!sel) return
    let stop = false
    const load = async () => {
      try {
        const d = await getAgentBrief(sel.id)
        if (!stop) setState({ loading: false, ...d })
      } catch (e) {
        if (!stop) setState({ loading: false, error: e?.message || 'failed to load brief' })
      }
    }
    setState({ loading: true })
    load()
    const id = setInterval(load, 5000) // keep it live while the modal is open
    const onEsc = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onEsc)
    return () => {
      stop = true
      clearInterval(id)
      document.removeEventListener('keydown', onEsc)
    }
  }, [sel, onClose])

  if (!sel) return null
  const desc = (sel.card_summary && sel.card_summary.description) || ''
  const tools = (sel.card_summary && sel.card_summary.skills) || sel.skills || []
  const fw = sel.framework || sel.runtime || ''
  const kindWord =
    { agent_framework: 'agent', orchestrator: 'orchestrator', mcp_server: 'MCP server', llm_runtime: 'LLM runtime' }[
      sel.kind
    ] ||
    sel.kind ||
    ''
  const risk = sel.authoritative_risk || sel.risk || ''
  return (
    <div
      className="fixed inset-0 z-[80] grid place-items-center bg-black/40 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-card-hover dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-lg font-bold text-brand-900 dark:text-slate-100">{sel.name}</div>
            <div className="truncate font-mono text-[11px] text-slate-400">{sel.id}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-lg text-slate-400 transition hover:bg-slate-100 hover:text-brand-600"
          >
            ✕
          </button>
        </div>

        {/* meta chips: framework · kind · risk tier · status */}
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px]">
          {fw && <MetaChip>{fw}</MetaChip>}
          {kindWord && <MetaChip>{kindWord}</MetaChip>}
          {risk && (
            <MetaChip>
              risk <b className="font-semibold">{risk}</b>
            </MetaChip>
          )}
          {sel.status && <MetaChip>{sel.status}</MetaChip>}
        </div>

        {/* What the agent is about — the agent card's own description (declared in its
            JSON), rendered verbatim so multi-line descriptions keep their formatting. */}
        <section className="mt-4">
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">About</h4>
          {desc ? (
            <p className="whitespace-pre-line text-sm leading-relaxed text-slate-700 dark:text-slate-200">{desc}</p>
          ) : (
            <p className="text-sm italic text-slate-400">No description declared for this agent.</p>
          )}
        </section>

        {/* Tools it declares — the formatted section */}
        <section className="mt-4">
          <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">
            Tools <span className="font-normal text-slate-400">({tools.length})</span>
          </h4>
          {tools.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {tools.map((t) => (
                <li
                  key={t}
                  className="rounded-md bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  {t}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm italic text-slate-400">No declared tools yet.</p>
          )}
        </section>

        {/* Live status — ONLY the dynamic parts (what it's doing + liveness); identity,
            framework, tools and risk are already shown above, so they're dropped here. */}
        <section className="mt-4">
          <h4 className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">
            Live status
            {!state.loading && !state.error && (
              <span className="inline-flex items-center gap-1 text-[10px] font-normal normal-case text-slate-400">
                <span className={`h-1.5 w-1.5 rounded-full ${state.live ? 'bg-brand-500' : 'bg-rose-500'}`} />
                {state.live ? 'live' : 'stopped'} · refreshes every 5s
              </span>
            )}
          </h4>
          <div className="rounded-xl bg-slate-50 p-4 text-sm leading-relaxed text-slate-700 dark:bg-slate-800/50 dark:text-slate-200">
            {state.loading ? (
              <span className="text-slate-400">Generating a live brief…</span>
            ) : state.error ? (
              <span className="text-rose-600">{state.error}</span>
            ) : state.activity || state.liveness ? (
              <div className="space-y-1">
                {state.activity && <p>{state.activity}</p>}
                {state.liveness && <p className="text-[13px] text-slate-500 dark:text-slate-400">{state.liveness}</p>}
              </div>
            ) : (
              // Fallback for an older backend that only returns the combined `brief`.
              state.brief
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

// A small pill for a single agent-meta fact (framework, kind, risk tier, status).
// Brand-green tint so it reads as part of the app, not a cold slate/blue chip.
function MetaChip({ children }) {
  return (
    <span className="rounded-full bg-brand-50 px-2 py-0.5 text-brand-700 ring-1 ring-inset ring-brand-600/15 dark:bg-brand-900/40 dark:text-brand-200 dark:ring-brand-400/20">
      {children}
    </span>
  )
}

// ── Tool Risk Tiers ────────────────────────────────────────────────────────────
// A risk-tier bar for EVERY tool call the worker agents make. The hidden OPA agent
// reasons each call's tier with a small language model (low/medium/high/severe); we
// render it as a level-meter bar so operators see, per call, how risky each tool is.
// The SLM owns each tier; the operator owns ONE control — a block-threshold slider:
// any call at or above the chosen tier blocks the question in the user UI. Radar-only.
function ToolTiers({ tierData, onRefresh }) {
  const enabled = !!tierData?.enabled
  const tiers = tierData?.tiers || []
  const recent = tierData?.recent || []
  const slm = tierData?.slm || {}
  const serverThreshold = tierData?.block_threshold || ''

  // The slider's tier. Mirrors the server, but is driven locally while dragging so
  // the meter feels responsive; the next poll (or our refresh) reconciles it.
  const [threshold, setThreshold] = useState(serverThreshold)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    if (serverThreshold) setThreshold(serverThreshold)
  }, [serverThreshold])

  const idx = Math.max(0, tiers.indexOf(threshold))

  async function commitThreshold(next) {
    if (!next || next === serverThreshold) return
    const prev = serverThreshold || threshold
    setThreshold(next) // optimistic
    setSaving(true)
    setErr('')
    try {
      await setBlockThreshold(next)
      onRefresh && onRefresh() // pull the server's truth back so the bars reconcile
    } catch (e) {
      // Surface the failure instead of swallowing it (e.g. OPA agent not restarted
      // with the /threshold route) and revert so the UI never lies about the cutoff.
      setThreshold(prev)
      setErr(e?.message || 'failed to set threshold')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel
      title="Tool Risk Tiers"
      subtitle="Every tool call the agents make, risk-tiered by a small language model — calls at or above the block threshold are blocked"
      right={
        enabled ? (
          // The block-threshold slider lives INLINE in the header — it fills the gap
          // between the title and the gemma label on the right.
          <div className="flex flex-1 items-center gap-4 pl-8">
            {/* caption + slider + the tier names labelled beneath it, aligned to each stop */}
            <div className="flex flex-1 flex-col gap-1">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                allowed risk level
              </span>
              <input
                type="range"
                min={0}
                max={Math.max(0, tiers.length - 1)}
                step={1}
                value={idx}
                disabled={saving || tiers.length === 0}
                title={`block at or above ${threshold || '—'}`}
                aria-label="Block threshold"
                onChange={(e) => setThreshold(tiers[Number(e.target.value)] || threshold)}
                onPointerUp={(e) => commitThreshold(tiers[Number(e.currentTarget.value)])}
                onKeyUp={(e) => commitThreshold(tiers[Number(e.currentTarget.value)])}
                className="risk-slider w-full"
                style={{
                  // track filled with the current tier's colour BEFORE the thumb, white AFTER
                  background: `linear-gradient(90deg, ${tierHex(threshold)} 0%, ${tierHex(threshold)} ${
                    (idx / Math.max(1, tiers.length - 1)) * 100
                  }%, #ffffff ${(idx / Math.max(1, tiers.length - 1)) * 100}%, #ffffff 100%)`,
                  // square thumb filled with the currently-selected tier's colour
                  '--thumb-color': tierHex(threshold),
                }}
              />
              <div className="flex justify-between text-[10px]">
                {tiers.map((t, i) => {
                  const s = tierStyle(t, tiers)
                  // each name carries its tier's colour; the selected one is bolded
                  return (
                    <span key={t} className={`${s.text} ${i === idx ? 'font-bold' : 'font-medium'}`}>
                      {t}
                    </span>
                  )
                })}
              </div>
            </div>
            <span className="flex shrink-0 items-center gap-3 whitespace-nowrap text-xs text-slate-500">
              {slm.model && (
                <span className="font-mono text-slate-400" title={`SLM @ ${slm.base || ''}`}>
                  {slm.model}
                </span>
              )}
              {err ? (
                <span className="text-rose-600" title={err}>⚠ not saved</span>
              ) : saving ? (
                <span className="text-slate-400">saving…</span>
              ) : null}
            </span>
          </div>
        ) : (
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-500">OPA agent off</span>
        )
      }
    >
      {!enabled ? (
        <div className="px-5 py-6 text-sm text-slate-400">
          The OPA agent isn’t wired to the gateway — start it and set{' '}
          <span className="font-mono text-xs">AWCP_OPA_AGENT_URL</span> so tool calls get SLM-reasoned tiers.
        </div>
      ) : (
        <>
          <Table columns={['When', 'Agent', 'Tool', 'Risk tier', 'Decision']}>
            {recent.length === 0 ? (
              <EmptyRow colSpan={5}>
                No tool calls yet — ask a question in the chat UI and every agent tool call appears here.
              </EmptyRow>
            ) : (
              recent.map((c, i) => {
                const blocked = c.decision === 'block'
                return (
                  <tr
                    key={`${c.ts}-${i}`}
                    className={blocked ? 'bg-rose-50/40 dark:bg-rose-500/10' : 'hover:bg-slate-50'}
                  >
                    <Td className="whitespace-nowrap text-xs text-slate-500">{timeAgo(c.ts)}</Td>
                    <Td className="font-mono text-xs text-slate-500">{c.agent_id || '—'}</Td>
                    <Td>
                      <span className="font-mono text-xs text-slate-700" title={c.reasoning || ''}>
                        {c.tool_name}
                      </span>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <TierBar tier={c.risk_tier} tiers={tiers} />
                        <span
                          className={`text-xs font-semibold ${tierStyle(c.risk_tier, tiers).text}`}
                          title={c.reasoning || ''}
                        >
                          {c.risk_tier}
                        </span>
                      </div>
                    </Td>
                    <Td>
                      {blocked ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-xs font-semibold text-rose-700 ring-1 ring-inset ring-rose-600/20 dark:bg-rose-500/25 dark:text-rose-200 dark:ring-rose-400/40">
                          ⛔ blocked
                        </span>
                      ) : (
                        <span className="text-xs text-brand-600 dark:text-brand-300">allowed</span>
                      )}
                    </Td>
                  </tr>
                )
              })
            )}
          </Table>
        </>
      )}
    </Panel>
  )
}
