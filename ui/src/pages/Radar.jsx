import { useEffect, useState } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import { getAgents, getToolTiers, setBlockThreshold } from '../api.js'
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

  return (
    <div className="space-y-6">
      <ToolTiers tierData={tierData} onRefresh={refreshTiers} />

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
                // very light red so they read as "stopped, not gone"
                className={a.alive ? 'hover:bg-slate-50' : 'bg-rose-50 hover:bg-rose-100'}
              >
                <Td>
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium text-brand-900">{a.name}</span>
                    {a.card_summary && (
                      <span
                        className="rounded bg-brand-100 px-1.5 py-0.5 text-[10px] font-medium text-brand-700 ring-1 ring-inset ring-brand-600/20"
                        title={a.card_summary.description
                          ? `AgentCard: ${a.card_summary.description}`
                          : 'AgentCard published'}
                      >
                        card
                      </span>
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
                    <span className={a.alive ? 'text-brand-600' : 'text-rose-600'}>
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
    </div>
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
                  <tr key={`${c.ts}-${i}`} className={blocked ? 'bg-rose-50/40' : 'hover:bg-slate-50'}>
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
                        <span className="rounded-full bg-rose-100 px-2 py-0.5 text-xs font-semibold text-rose-700">
                          ⛔ blocked
                        </span>
                      ) : (
                        <span className="text-xs text-brand-600">allowed</span>
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
