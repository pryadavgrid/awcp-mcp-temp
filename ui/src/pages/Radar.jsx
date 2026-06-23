import { usePoll } from '../hooks/usePoll.js'
import { getAgents, getToolTiers } from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { StatusBadge } from '../components/Badge.jsx'
import { timeAgo } from '../lib/format.js'

// Tier → colour. Known names are styled directly; any other (env-driven) vocabulary
// falls back to a position-based ramp (first = safe/green … last = severe/red) so the
// bars work for ANY tier list without hardcoding the names.
const TIER_STYLE = {
  low: { text: 'text-emerald-600', fill: 'bg-emerald-500' },
  medium: { text: 'text-amber-600', fill: 'bg-amber-500' },
  high: { text: 'text-orange-600', fill: 'bg-orange-500' },
  severe: { text: 'text-rose-600', fill: 'bg-rose-500' },
}
const RAMP = [
  { text: 'text-emerald-600', fill: 'bg-emerald-500' },
  { text: 'text-amber-600', fill: 'bg-amber-500' },
  { text: 'text-orange-600', fill: 'bg-orange-500' },
  { text: 'text-rose-600', fill: 'bg-rose-500' },
]

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

export default function Radar() {
  const { data, loading } = usePoll(getAgents, [])
  const agents = data || []
  const { data: tierData } = usePoll(getToolTiers, [])

  return (
    <div className="space-y-6">
      <ToolTiers tierData={tierData} />

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
    </div>
  )
}

// ── Tool Risk Tiers ────────────────────────────────────────────────────────────
// A risk-tier bar for EVERY tool call the worker agents make. The hidden OPA agent
// reasons each call's tier with a small language model (low/medium/high/severe); we
// render it as a level-meter bar so operators see, per call, how risky each tool is.
// Read-only and live — the SLM owns the tier, there are no controls. Radar-only.
function ToolTiers({ tierData }) {
  const enabled = !!tierData?.enabled
  const tiers = tierData?.tiers || []
  const blockTiers = tierData?.block_tiers || []
  const recent = tierData?.recent || []
  const slm = tierData?.slm || {}

  return (
    <Panel
      title="Tool Risk Tiers"
      subtitle="Every tool call the agents make, risk-tiered by a small language model — high/severe calls are blocked"
      right={
        enabled ? (
          <span className="flex items-center gap-3 text-xs text-slate-500">
            {slm.model && (
              <span className="font-mono text-slate-400" title={`SLM @ ${slm.base || ''}`}>
                {slm.model}
              </span>
            )}
            <span>
              blocks <span className="font-mono text-rose-600">{blockTiers.join(', ') || '—'}</span>
            </span>
          </span>
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
          {/* legend: the tier scale */}
          <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-2.5 text-xs">
            <span className="text-slate-400">tiers:</span>
            {tiers.map((t) => {
              const s = tierStyle(t, tiers)
              const blocks = blockTiers.includes(t)
              return (
                <span key={t} className="flex items-center gap-1.5">
                  <span className={`h-2 w-4 rounded-sm ${s.fill}`} />
                  <span className={`font-medium ${s.text}`}>{t}</span>
                  {blocks && <span className="text-[10px] text-rose-500">⛔</span>}
                </span>
              )
            })}
          </div>

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
                        <span className="text-xs text-emerald-600">allowed</span>
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
