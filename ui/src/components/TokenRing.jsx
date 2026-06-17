// Circular token-budget ratio for the selected agent — a bar (ring) only, no
// number. The arc length is the % of the sliding-window token budget consumed.
// Data is the same as TokenBar (live /laminar/usage, or the agent's resolved
// budget at 0 used); `pending` → agent not started yet (faint ring). The exact
// counts are still available on hover via the title. Everything is derived from
// props, so any agent / any budget renders correctly.

const R = 12
const CIRC = 2 * Math.PI * R

export default function TokenRing({ usage, pending, agentName }) {
  const b = usage && usage.budget
  const used = b ? b.used_tokens ?? 0 : 0
  const budget = b ? b.budget_tokens || 0 : 0
  const pct = b ? Math.min(100, Math.round((b.ratio || 0) * 100)) : 0
  const state = pending ? 'idle' : (b && b.state) || 'idle'
  const offset = CIRC * (1 - pct / 100)

  const title = pending
    ? `${agentName || 'This agent'} hasn't started — run it once and its token budget appears here.`
    : budget
      ? `${used.toLocaleString()} / ${budget.toLocaleString()} tokens used (${pct}%) — ${state}`
      : 'Token usage unavailable'

  return (
    <div className={`token-ring tr-${state}`} title={title} aria-label={title}>
      <svg width="30" height="30" viewBox="0 0 30 30">
        <circle className="tr-track" cx="15" cy="15" r={R} fill="none" strokeWidth="3.5" />
        <circle
          className="tr-bar"
          cx="15"
          cy="15"
          r={R}
          fill="none"
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeDasharray={CIRC}
          strokeDashoffset={offset}
          transform="rotate(-90 15 15)"
        />
      </svg>
    </div>
  )
}
