import { useEffect, useState } from 'react'

// Instead of listing raw governed steps (llm_called, tool_call, …), show ONE
// friendly line that types itself out — "Thinking…", "Searching the web…",
// "Analysing…" — like a chat assistant working. The phrase is driven by the
// step the agent is currently on (read from the Temporal timeline), so it still
// reflects what's really happening; it's just shown in plain language.

const PHRASES = {
  setup: 'Getting started',
  llm_called: 'Thinking',
  web_search: 'Searching the web',
  tool_called: 'Working on it',
  synthesize: 'Analysing',
  complete: 'Wrapping up',
}

const TERMINAL = ['done', 'failed', 'blocked']

// The step the agent is currently on: the latest running/scheduled one, else the
// most recent step. Returns its kind (e.g. "web_search") or null when no steps.
function currentKind(items) {
  if (!items || items.length === 0) return null
  const inflight = [...items]
    .reverse()
    .find((it) => it.status === 'running' || it.status === 'scheduled')
  return (inflight || items[items.length - 1]).kind
}

// Typewriter: reveal `text` one character at a time while `animate` is true.
function useTypewriter(text, animate) {
  const [shown, setShown] = useState(animate ? '' : text)
  useEffect(() => {
    if (!animate) {
      setShown(text)
      return
    }
    setShown('')
    let i = 0
    const id = setInterval(() => {
      i += 1
      setShown(text.slice(0, i))
      if (i >= text.length) clearInterval(id)
    }, 45)
    return () => clearInterval(id)
  }, [text, animate])
  return shown
}

export default function Timeline({ items, status }) {
  const active = status && !TERMINAL.includes(status)
  const kind = currentKind(items)
  const phrase = (kind && PHRASES[kind]) || (active ? 'Thinking' : '')
  const typed = useTypewriter(phrase, active)

  if (!active) {
    if (status === 'done') return <div className="think done">✓ Done</div>
    if (status === 'failed') return <div className="think failed">✕ Couldn’t finish</div>
    if (status === 'blocked') return <div className="think failed">⛔ Blocked by the control plane</div>
    return <div className="think muted">Ready when you are.</div>
  }

  return (
    <div className="think" aria-live="polite">
      <span className="think-text">{typed}</span>
      <span className="think-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </div>
  )
}
