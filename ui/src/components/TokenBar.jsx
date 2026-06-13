// Live token-budget bar for the selected agent — how many tokens it has used
// and how many remain in the current sliding window. Data comes from the
// gateway's /laminar/usage; renders nothing until the agent has reported usage.
export default function TokenBar({ usage }) {
  if (!usage || !usage.budget) return null
  const b = usage.budget
  const w = usage.window || {}
  const used = b.used_tokens ?? w.total_tokens ?? 0
  const budget = b.budget_tokens || 0
  const remaining = Math.max(0, budget - used)
  const pct = Math.min(100, Math.round((b.ratio || 0) * 100))
  const state = b.state || 'ok' // ok | warn | exhausted
  const fmt = (n) => Number(n || 0).toLocaleString()

  return (
    <div className={`tokenbar tb-${state}`}>
      <div className="tb-head">
        <span className="tb-lbl">Token budget · sliding window</span>
        <span className={`tb-state tb-s-${state}`}>{state}</span>
      </div>
      <div className="tb-track">
        <i style={{ width: pct + '%' }} />
      </div>
      <div className="tb-nums mono">
        <span><b>{fmt(used)}</b> used</span>
        <span className="tb-rem"><b>{fmt(remaining)}</b> remaining</span>
        <span className="tb-tot">{fmt(budget)} budget · {pct}% · {w.calls || 0} calls</span>
      </div>
    </div>
  )
}
