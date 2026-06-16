// Live token-budget bar for the selected agent — how many tokens it has used
// and how many remain in the current sliding window. Data comes from the
// gateway's /laminar/usage (when the agent has spent) or its resolved budget
// (when it's registered but hasn't spent yet). `pending` is set when the agent
// has no control-plane identity yet (not started), so we still show the slot.
export default function TokenBar({ usage, pending, agentName }) {
  if (pending) {
    return (
      <div className="tokenbar tb-pending">
        <div className="tb-head">
          <span className="tb-lbl">Token budget · sliding window</span>
          <span className="tb-state tb-s-idle">not started</span>
        </div>
        <div className="tb-note">
          Run {agentName ? `“${agentName}”` : 'this agent'} once — it registers with
          the control plane and its token budget appears here.
        </div>
      </div>
    )
  }
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
