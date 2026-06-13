// Live step timeline. Each item is one Temporal activity the agent triggered,
// streamed back from the gateway. The kind/label/detail are all data-driven, so
// a brand-new step type the backend starts emitting still renders (it just gets
// the fallback icon) — nothing here is hardcoded to a fixed set of steps.

const ICONS = {
  setup: '🧩',
  llm_called: '🧠',
  web_search: '🔎',
  tool_called: '⚙️',
  synthesize: '✨',
  complete: '✅',
}

function detailLine(it) {
  const bits = []
  if (it.tool_name) bits.push(it.tool_name)
  if (it.model) bits.push(it.model)
  if (it.query) bits.push(`“${it.query}”`)
  if (it.risk) bits.push(`risk ${it.risk}`)
  if (it.gate && it.gate !== 'allowed') bits.push(`gate ${it.gate}`)
  return bits.join('  ·  ')
}

export default function Timeline({ items, status }) {
  const active = status && !['done', 'failed', 'blocked'].includes(status)

  if (!items || items.length === 0) {
    return (
      <div className="tl-empty">
        {active ? (
          <>
            <span className="spinner" /> Waiting for the agent to start working…
          </>
        ) : (
          'No steps recorded yet.'
        )}
      </div>
    )
  }

  return (
    <div className="timeline">
      {items.map((it) => (
        <div className={`tl-item s-${it.status}`} key={it.seq}>
          <div className="tl-rail">
            <div className="tl-dot">{ICONS[it.kind] || '•'}</div>
          </div>
          <div className="tl-card">
            <div className="tl-head">
              <span className="tl-label">{it.label}</span>
              <span className={`tl-status s-${it.status}`}>
                {it.status === 'running' && <span className="spinner sm" />}
                {it.status}
              </span>
            </div>
            {detailLine(it) && <div className="tl-detail">{detailLine(it)}</div>}
          </div>
        </div>
      ))}
      {active && (
        <div className="tl-item s-pending">
          <div className="tl-rail">
            <div className="tl-dot pending">
              <span className="spinner" />
            </div>
          </div>
          <div className="tl-card muted">working…</div>
        </div>
      )}
    </div>
  )
}
