import { useEffect, useRef, useState } from 'react'

// Agent selector that lives on the left of the composer. The list is whatever
// the gateway returns from GET /user/agents — every folder in the agent bundle,
// discovered live — so adding an agent folder makes it appear here with no code
// change. Renders as a popover menu (no fixed/hardcoded agent set).
export default function AgentMenu({ agents, selectedId, onSelect, disabled }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const onEsc = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  const selected = agents.find((a) => a.id === selectedId) || null
  const label = selected ? selected.name || selected.id : 'Select agent'

  return (
    <div className="agent-menu" ref={ref}>
      <button
        type="button"
        className="am-trigger"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        title={selected ? `${label}${selected.running ? ' · running' : ' · stopped'}` : 'Choose an agent'}
      >
        <span className={`am-dot ${selected && selected.running ? 'on' : ''}`} />
        <span className="am-label">{label}</span>
        <span className="am-caret" aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="am-pop" role="listbox">
          {agents.length === 0 && <div className="am-empty">No agents found</div>}
          {agents.map((a) => {
            const sub = [a.framework, a.model].filter(Boolean).join(' · ')
            return (
              <button
                type="button"
                key={a.id}
                role="option"
                aria-selected={a.id === selectedId}
                className={`am-item ${a.id === selectedId ? 'sel' : ''}`}
                onClick={() => {
                  onSelect(a.id)
                  setOpen(false)
                }}
              >
                <span className={`am-dot ${a.running ? 'on' : ''}`} />
                <span className="am-item-main">
                  <span className="am-item-name">{a.name || a.id}</span>
                  {sub && <span className="am-item-sub">{sub}</span>}
                </span>
                {a.running && <span className="am-run">running</span>}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
