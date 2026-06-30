import { useEffect, useMemo, useRef, useState } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import { getPolicy, putPolicy, getAgents } from '../api.js'
import { Panel, Table, Td, EmptyRow } from '../components/Table.jsx'
import { Badge, StatusBadge } from '../components/Badge.jsx'

// A starter document shown when nothing is stored yet, so the operator has the
// shape in front of them. Mirrors src/awcp/radar/policy.example.json.
const TEMPLATE = {
  version: 1,
  updated_by: 'operator',
  note: 'demonstrates every agent + tool capability',
  agents: {
    '*temporal*': { allow: false, note: 'EXPLICIT DENY — infra, never recognised' },
    Python: { allow: false, note: 'EXPLICIT DENY — bare interpreter' },
    'agent-crewai-*': { allow: true, note: 'EXPLICIT ALLOW — whitelist past the slider' },
    'agent-langgraph-*': { risk: 'low', note: 'RISK OVERRIDE — relabel to low so the slider lets it through' },
    'agent-pydantic_ai-*': { allow: 'default', note: 'DEFAULT — defer to the slider' },
  },
  tools: {
    run_command: { allow: false, note: 'EXPLICIT DENY — shell exec' },
    external_post: { allow: false, note: 'EXPLICIT DENY — outbound write' },
    web_search: { allow: true, note: 'EXPLICIT ALLOW — whitelist past the slider' },
    save_artifact: { risk: 'medium', note: 'RISK OVERRIDE — relabel tier' },
    search_arxiv: { risk: 'default', note: 'DEFAULT — SLM tier + slider decide' },
  },
}

// ── lightweight JSON syntax highlighting ──────────────────────────────────────
// Renders the editor text as coloured HTML shown UNDER a transparent textarea, so
// colouring tracks typing live without a heavy editor dependency. Restrained
// palette: property keys, strings, numbers, booleans/null, punctuation. Strings are
// matched whole (their contents are never re-tokenised), and everything is
// HTML-escaped first, so this can't break the markup or mis-highlight.
// Two palettes: dark-on-light for the light theme, and bright-on-black for the
// dark theme (so the editor reads like a real terminal — black background, vivid
// colour-coded tokens). The active one is chosen at render time from the theme.
// VS Code-style token colouring: a distinct hue per entry type. Same hue families
// in both themes (blue keys · green strings · amber numbers · violet literals ·
// slate punctuation), tuned dark-on-light for the light sheet and bright-on-black
// for the dark terminal.
const _HL_LIGHT = {
  key: '#1d4ed8',    // blue-700 — property names
  str: '#047857',    // emerald-700 — string values
  num: '#b45309',    // amber-700 — numbers
  lit: '#7c3aed',    // violet-600 — true/false/null
  punct: '#64748b',  // slate-500 — { } [ ] , :
}
const _HL_DARK = {
  key: '#4fc1ff',    // bright blue — property names
  str: '#5fd38d',    // bright green — string values
  num: '#dcb46a',    // gold — numbers
  lit: '#c191e8',    // bright violet — true/false/null
  punct: '#9aa6b2',  // light slate — { } [ ] , :
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
}

// Colour one line of JSON. (Strings never span lines in valid JSON, so a per-line
// pass is safe and lets us style the error line independently.)
function highlightLine(line, hl) {
  return escapeHtml(line).replace(
    /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}[\],:])/g,
    (m, str, colon, lit, num, punct) => {
      if (str !== undefined) {
        if (colon !== undefined) {
          return `<span style="color:${hl.key}">${str}</span><span style="color:${hl.punct}">${colon}</span>`
        }
        return `<span style="color:${hl.str}">${str}</span>`
      }
      if (lit !== undefined) return `<span style="color:${hl.lit}">${lit}</span>`
      if (num !== undefined) return `<span style="color:${hl.num}">${num}</span>`
      if (punct !== undefined) return `<span style="color:${hl.punct}">${punct}</span>`
      return m
    },
  )
}

// Render every line as a block, so the offending line (1-based `errorLine`, or null
// when the JSON is valid) gets a soft red wash + a red wavy underline — an IDE-style
// error marker that lines up exactly with the line number in the gutter.
const _ERR_STYLE =
  'display:block;background:rgba(239,68,68,0.13);' +
  'text-decoration:underline wavy #ef4444;text-underline-offset:3px;text-decoration-skip-ink:none'

function highlightJsonLines(src, errorLine, hl) {
  return src
    .split('\n')
    .map((ln, i) => {
      const inner = highlightLine(ln, hl) || ' ' // keep empty lines at full row height
      const style = i + 1 === errorLine ? _ERR_STYLE : 'display:block'
      return `<span style="${style}">${inner}</span>`
    })
    .join('')
}

export default function Policy() {
  const { data, loading, refresh } = usePoll(getPolicy, [])
  const { data: agentsData } = usePoll(getAgents, [])
  const agents = agentsData || []

  const [text, setText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState(null) // { tone:'green'|'red', text }
  const [saving, setSaving] = useState(false)
  const fileRef = useRef(null)
  const taRef = useRef(null)
  const gutterRef = useRef(null)
  const preRef = useRef(null)

  // Track the app's dark theme by watching the `dark` class on <html> (set by the
  // theme toggle). Used to switch the editor between a light sheet and a black
  // terminal with bright syntax colours. A MutationObserver keeps it reactive even
  // though the toggle lives in another component.
  const [isDark, setIsDark] = useState(() =>
    typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  )
  useEffect(() => {
    const el = document.documentElement
    const update = () => setIsDark(el.classList.contains('dark'))
    update()
    const obs = new MutationObserver(update)
    obs.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])

  // Line numbers for the gutter, derived from the current text.
  const lineCount = text.length ? text.split('\n').length : 1
  const lineNumbers = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n')

  // Live JSON validity → the 1-based line of the parse error (or null when valid /
  // empty), so the editor can mark that line red as the operator types.
  const errorLine = useMemo(() => {
    if (!text.trim()) return null
    try {
      JSON.parse(text)
      return null
    } catch (e) {
      const m = /position (\d+)/.exec(e.message || '')
      return m ? text.slice(0, Number(m[1])).split('\n').length : null
    }
  }, [text])

  // The highlighted HTML for the editor's underlay (colours + red error line).
  // Palette follows the theme: bright-on-black in dark mode, dark-on-light in light.
  const highlightedHtml = useMemo(
    () => highlightJsonLines(text, errorLine, isDark ? _HL_DARK : _HL_LIGHT),
    [text, errorLine, isDark],
  )

  // Turn a JSON.parse error into a line/column the operator can jump to. V8's
  // message carries a character "position N"; we map it back to line+col so the
  // gutter number is actionable. Falls back to the raw message if no position.
  function jsonError(e, src) {
    const m = /position (\d+)/.exec(e.message || '')
    if (m) {
      const pos = Number(m[1])
      const before = src.slice(0, pos)
      const line = before.split('\n').length
      const col = pos - before.lastIndexOf('\n')
      return `invalid JSON at line ${line}, col ${col}: ${e.message}`
    }
    return `invalid JSON: ${e.message}`
  }

  // Seed the editor from the store ONCE (and on an external reload), but never
  // clobber an in-progress edit on the 3s poll.
  useEffect(() => {
    if (data && !dirty) {
      const doc = data.stored ? data.policy : TEMPLATE
      setText(JSON.stringify(doc || {}, null, 2))
    }
  }, [data, dirty])

  const meta = data?.stored
    ? `stored · v${data.version} · ${data.enabled ? 'active' : 'inert'} · by ${data.updated_by || '—'}`
    : 'no policy stored — defaults apply (inert)'

  function format() {
    try {
      setText(JSON.stringify(JSON.parse(text), null, 2))
      setMsg({ tone: 'green', text: 'formatted' })
    } catch (e) {
      setMsg({ tone: 'red', text: jsonError(e, text) })
    }
  }

  // Tab indents instead of moving focus, so the JSON editor behaves like a code
  // editor. Tab: insert 2 spaces at the cursor, or indent every line in a multi-line
  // selection. Shift+Tab: dedent the current/selected lines. 2 spaces matches
  // JSON.stringify(…, 2). Selection is restored after React re-renders the value.
  function handleEditorKeyDown(e) {
    if (e.key !== 'Tab') return
    e.preventDefault()
    const INDENT = '  '
    const ta = e.target
    const { selectionStart: start, selectionEnd: end, value } = ta
    const lineStart = value.lastIndexOf('\n', start - 1) + 1
    const restore = (s, end2) => requestAnimationFrame(() => ta.setSelectionRange(s, end2))

    // Plain Tab with no selection -> just insert an indent at the cursor.
    if (!e.shiftKey && start === end) {
      setText(value.slice(0, start) + INDENT + value.slice(end))
      setDirty(true)
      restore(start + INDENT.length, start + INDENT.length)
      return
    }

    // Otherwise indent / dedent every line touched by the selection.
    const lines = value.slice(lineStart, end).split('\n')
    let firstDelta = 0
    let totalDelta = 0
    const out = lines.map((ln, i) => {
      if (e.shiftKey) {
        const removed = (ln.match(/^( {1,2}|\t)/) || [''])[0].length
        if (i === 0) firstDelta = -removed
        totalDelta -= removed
        return ln.slice(removed)
      }
      if (i === 0) firstDelta = INDENT.length
      totalDelta += INDENT.length
      return INDENT + ln
    })
    setText(value.slice(0, lineStart) + out.join('\n') + value.slice(end))
    setDirty(true)
    restore(Math.max(lineStart, start + firstDelta), end + totalDelta)
  }

  function reload() {
    setDirty(false)
    setMsg(null)
    refresh()
  }

  // Import a JSON file from the operator's machine into the editor. Pure
  // client-side (FileReader) — nothing is uploaded until they click Save. We
  // validate it parses and pretty-print it; bad files are reported, not loaded.
  async function importFile(e) {
    const file = e.target.files?.[0]
    e.target.value = '' // allow re-importing the same filename
    if (!file) return
    try {
      const raw = await file.text()
      const doc = JSON.parse(raw) // reject non-JSON before touching the editor
      setText(JSON.stringify(doc, null, 2))
      setDirty(true)
      setMsg({ tone: 'green', text: `imported ${file.name} — review, then Save` })
    } catch (err) {
      setMsg({ tone: 'red', text: `import failed (${file.name}): ${jsonError(err, '')}` })
    }
  }

  async function save() {
    let doc
    try {
      doc = JSON.parse(text)
    } catch (e) {
      setMsg({ tone: 'red', text: jsonError(e, text) })
      return
    }
    setSaving(true)
    setMsg(null)
    try {
      const r = await putPolicy(doc)
      setDirty(false)
      setMsg({ tone: 'green', text: `✓ saved v${r.version} (${r.enabled ? 'active' : 'inert'})` })
      refresh()
    } catch (e) {
      // The backend returns 400 (bad shape) or 503 (no governance DB) with a detail.
      setMsg({ tone: 'red', text: `✗ ${e.message}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <Panel
        title="Operator Policy"
        subtitle="Operator overrides for which agents and tools are allowed, and at what risk tier — applied on top of the OPA-assigned baseline."
        right={<span className="text-xs text-slate-500">{meta}</span>}
      >
        <div className="space-y-4 px-5 py-4">
          <dl className="grid grid-cols-[6.5rem_1fr] gap-x-4 gap-y-2 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs">
            <dt className="font-mono text-[11px] text-slate-500">default</dt>
            <dd className="text-slate-600">
              Allowed when the tier is <span className="font-medium">below</span> the “allowed risk level” slider;
              denied at or above it.
            </dd>
            <dt className="font-mono text-[11px] text-slate-500">allow</dt>
            <dd className="text-slate-600">
              <code className="rounded bg-white px-1 font-mono ring-1 ring-inset ring-slate-200">true</code> always
              allow, <code className="rounded bg-white px-1 font-mono ring-1 ring-inset ring-slate-200">false</code>{' '}
              always deny — overrides the slider. Omit to let the slider decide.
            </dd>
            <dt className="font-mono text-[11px] text-slate-500">risk</dt>
            <dd className="text-slate-600">
              Relabels the tier compared against the slider — agents{' '}
              <span className="font-mono">low·medium·high</span>, tools also <span className="font-mono">severe</span>.
            </dd>
            <dt className="font-mono text-[11px] text-slate-500">skills</dt>
            <dd className="text-slate-600">
              Match agents by a card-declared skill (Skills column below). Can only{' '}
              <span className="font-medium">tighten</span> — <code className="rounded bg-white px-1 font-mono ring-1 ring-inset ring-slate-200">allow:false</code> or raise risk — since skills are self-declared.
            </dd>
          </dl>

          {/* Code-style editor: a line-number gutter + live JSON syntax colouring.
              The colours are rendered in a <pre> UNDER a transparent textarea (the
              textarea handles input/caret/selection; the pre shows the colours). All
              three layers share the same font/size/line-height/padding and no-wrap,
              so colours, caret and line numbers line up exactly and scroll together. */}
          <div
            className={`flex h-80 overflow-hidden rounded-lg border focus-within:border-brand-500 ${
              isDark
                ? 'border-[#23302b] bg-[#0c1411]'
                : 'border-slate-300 bg-slate-50 focus-within:bg-white'
            }`}
          >
            <div
              ref={gutterRef}
              aria-hidden="true"
              className="select-none overflow-hidden whitespace-pre py-3 pl-3 pr-2 text-right font-mono text-[12.5px] leading-relaxed text-slate-400"
            >
              {lineNumbers}
            </div>
            <div className="relative flex-1 overflow-hidden">
              <pre
                ref={preRef}
                aria-hidden="true"
                className={`pointer-events-none absolute inset-0 m-0 overflow-hidden whitespace-pre py-3 pl-2 pr-3 font-mono text-[12.5px] leading-relaxed ${
                  isDark ? 'text-[#cfe3d6]' : 'text-slate-800'
                }`}
                dangerouslySetInnerHTML={{ __html: highlightedHtml }}
              />
              <textarea
                ref={taRef}
                value={text}
                spellCheck={false}
                wrap="off"
                // Keep the typed text invisible so the coloured <pre> underlay shows
                // through. Inline (not just the text-transparent class) so it beats
                // the global `.dark textarea { color }` rule, which would otherwise
                // paint the text opaque white in dark mode and hide the colours.
                style={{ color: 'transparent' }}
                onChange={(e) => {
                  setText(e.target.value)
                  setDirty(true)
                }}
                onKeyDown={handleEditorKeyDown}
                onScroll={(e) => {
                  const { scrollTop, scrollLeft } = e.target
                  if (gutterRef.current) gutterRef.current.scrollTop = scrollTop
                  if (preRef.current) {
                    preRef.current.scrollTop = scrollTop
                    preRef.current.scrollLeft = scrollLeft
                  }
                }}
                placeholder={loading ? 'Loading…' : '{ "agents": {...}, "tools": {...} }'}
                className={`absolute inset-0 m-0 resize-none overflow-auto whitespace-pre bg-transparent py-3 pl-2 pr-3 font-mono text-[12.5px] leading-relaxed text-transparent outline-none placeholder:text-slate-400 ${
                  isDark ? 'caret-[#7ee787]' : 'caret-slate-800'
                }`}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={save}
              disabled={saving}
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save policy'}
            </button>
            <button
              onClick={reload}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
            >
              Reload from store
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              onChange={importFile}
              className="hidden"
            />
            <button
              onClick={() => fileRef.current?.click()}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
            >
              Import JSON…
            </button>
            <button
              onClick={format}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
            >
              Format JSON
            </button>
            {msg && (
              <span
                className={`font-mono text-xs ${msg.tone === 'green' ? 'text-brand-600' : 'text-rose-600'}`}
              >
                {msg.text}
              </span>
            )}
            {dirty && !msg && <span className="text-xs text-amber-600">unsaved changes</span>}
          </div>
        </div>
      </Panel>

      <Panel
        title="Agent recognition"
        subtitle="How the active policy resolves for each detected agent — recognition and the authoritative risk tier (● marks an operator-policy override)"
        right={
          <span className="text-xs text-slate-500">
            {agents.length} agent{agents.length === 1 ? '' : 's'}
          </span>
        }
      >
        <Table columns={['Name', 'Status', 'Recognised', 'Risk (authoritative)', 'Skills']}>
          {agents.length === 0 ? (
            <EmptyRow colSpan={5}>No agents detected yet.</EmptyRow>
          ) : (
            agents.map((a) => (
              <tr key={a.id} className="hover:bg-slate-50">
                <Td>
                  <div className="font-medium text-brand-900">{a.name}</div>
                  <div className="font-mono text-[11px] text-slate-400">{a.id}</div>
                </Td>
                <Td>
                  <StatusBadge value={a.status} title={a.quarantine_reason || undefined} />
                </Td>
                <Td>
                  {a.recognised === true ? (
                    <Badge tone="green">recognised</Badge>
                  ) : a.recognised === false ? (
                    <Badge tone="red">not recognised</Badge>
                  ) : (
                    <span className="text-slate-400">— no rule</span>
                  )}
                </Td>
                <Td>
                  <span className="flex items-center gap-1.5">
                    <StatusBadge value={a.authoritative_risk || a.risk} />
                    {a.policy_risk && (
                      <span className="text-brand-600" title="operator-policy override">
                        ●
                      </span>
                    )}
                  </span>
                </Td>
                <Td>
                  {(() => {
                    const sk = (a.card_summary && a.card_summary.skills) || a.skills || []
                    if (!sk.length) return <span className="text-slate-400">—</span>
                    return (
                      <span className="flex flex-wrap gap-1" title={sk.join(', ')}>
                        {sk.slice(0, 4).map((s) => (
                          <span key={s} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">
                            {s}
                          </span>
                        ))}
                        {sk.length > 4 && <span className="text-[10px] text-slate-400">+{sk.length - 4}</span>}
                      </span>
                    )
                  })()}
                </Td>
              </tr>
            ))
          )}
        </Table>
      </Panel>
    </div>
  )
}
