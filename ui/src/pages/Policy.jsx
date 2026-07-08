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
    'agent-langgraph-*': {
      risk: 'low',
      note: 'RISK OVERRIDE — relabel to low so the slider lets it through',
      tools: {
        run_command: { risk: 'medium', active: true, note: 'PER-TOOL — trusted shell agent' },
        external_post: { active: false, note: 'PER-TOOL — but never post outbound' },
      },
    },
    'agent-pydantic_ai-*': { allow: 'default', note: 'DEFAULT — defer to the slider' },
  },
  tools: {
    run_command: { allow: false, risk: 'severe', note: 'EXPLICIT DENY — shell exec' },
    external_post: { allow: false, risk: 'high', note: 'EXPLICIT DENY — outbound write' },
    web_search: { allow: true, note: 'EXPLICIT ALLOW — whitelist past the slider' },
    save_artifact: { risk: 'medium', note: 'RISK OVERRIDE — relabel tier' },
    search_arxiv: { risk: 'default', note: 'DEFAULT — SLM tier + slider decide' },
  },
}

// A documented, mostly-empty policy the operator can download, fill in, and re-import.
// The `_README` lines (ignored by the engine — every '_'-prefixed key is) explain each
// field; the single example under agents/tools shows the exact shape + tier vocabulary.
const BLANK_TEMPLATE = {
  _README: [
    'AWCP Operator Policy — fill this in, then Import it (or paste into the JSON view) and Save.',
    '',
    'agents: WHICH detected agents are recognised, and at what risk tier.',
    "  key   = agent id or name, globs ok (e.g. 'agent-langgraph-*').",
    '  risk  = low | medium | high          (operator override of the baseline tier)',
    '  allow = true (always allow) | false (always deny) | omit (let the slider decide)',
    "  tools = OPTIONAL nested map — this agent's OWN tier/active for a specific tool,",
    '          overriding the overall tools entry for THIS agent only (see per-tool below).',
    '',
    'tools: the OVERALL allow + risk tier for a tool (used when an agent has no nested override).',
    "  key   = tool name, globs ok (e.g. 'run_command').",
    '  risk  = low | medium | high | severe',
    '  allow = true | false | omit',
    '',
    'per-tool (nested at agents.<agent>.tools.<tool>):',
    '  risk   = low | medium | high | severe',
    '  active = true (allow this tool for this agent) | false (deny) | omit',
    '',
    'ASSESSMENT ORDER for a tool call: nested agent-tool (risk/active) -> overall tool (risk/allow) -> OPA baseline tier.',
    'Omit any field (or use "default") to defer to the OPA baseline + slider. Keys starting with "_" are ignored.',
  ],
  version: 1,
  updated_by: 'your-name',
  note: 'describe this policy version',
  agents: {
    'agent-example-*': {
      risk: 'low',
      allow: true,
      tools: {
        run_command: { risk: 'medium', active: true },
      },
    },
  },
  tools: {
    run_command: { risk: 'severe', allow: false },
    web_search: { risk: 'low' },
  },
}

// Download BLANK_TEMPLATE as a .json file (no server round-trip — a client-side Blob).
function downloadTemplate() {
  const blob = new Blob([JSON.stringify(BLANK_TEMPLATE, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'awcp-policy-template.json'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// Risk-tier vocabularies + colours (shared with Radar's Tool Risk Tiers): agents
// go low·medium·high; tools also have severe. 'default' means "no override — use
// the OPA baseline + slider" and reads slate.
const AGENT_TIERS = ['low', 'medium', 'high']
const TOOL_TIERS = ['low', 'medium', 'high', 'severe']
const TIER_HEX = { low: '#22c55e', medium: '#f59e0b', high: '#f97316', severe: '#f43f5e' }
const DEFAULT_HEX = '#94a3b8' // slate-400 — "no opinion"
const tierHex = (t) => TIER_HEX[t] || DEFAULT_HEX

// ── lightweight JSON syntax highlighting ──────────────────────────────────────
// Renders the editor text as coloured HTML shown UNDER a transparent textarea, so
// colouring tracks typing live without a heavy editor dependency. Restrained
// palette: property keys, strings, numbers, booleans/null, punctuation. Strings are
// matched whole (their contents are never re-tokenised), and everything is
// HTML-escaped first, so this can't break the markup or mis-highlight.
// Two palettes: dark-on-light for the light theme, and bright-on-black for the
// dark theme (so the editor reads like a real terminal — black background, vivid
// colour-coded tokens). The active one is chosen at render time from the theme.
const _HL_LIGHT = {
  key: '#1d4ed8',
  str: '#047857',
  num: '#b45309',
  lit: '#7c3aed',
  punct: '#64748b',
}
const _HL_DARK = {
  key: '#4fc1ff',
  str: '#5fd38d',
  num: '#dcb46a',
  lit: '#c191e8',
  punct: '#9aa6b2',
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
}

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

const _ERR_STYLE =
  'display:block;background:rgba(239,68,68,0.13);' +
  'text-decoration:underline wavy #ef4444;text-underline-offset:3px;text-decoration-skip-ink:none'

function highlightJsonLines(src, errorLine, hl) {
  return src
    .split('\n')
    .map((ln, i) => {
      const inner = highlightLine(ln, hl) || ' '
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
  // Visual builder is the DEFAULT view; the toggle "slider" flips to the raw JSON.
  const [view, setView] = useState('builder')
  const fileRef = useRef(null)
  const taRef = useRef(null)
  const gutterRef = useRef(null)
  const preRef = useRef(null)

  // Track the app's dark theme by watching the `dark` class on <html>.
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

  const lineCount = text.length ? text.split('\n').length : 1
  const lineNumbers = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n')

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

  const highlightedHtml = useMemo(
    () => highlightJsonLines(text, errorLine, isDark ? _HL_DARK : _HL_LIGHT),
    [text, errorLine, isDark],
  )

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

  function handleEditorKeyDown(e) {
    if (e.key !== 'Tab') return
    e.preventDefault()
    const INDENT = '  '
    const ta = e.target
    const { selectionStart: start, selectionEnd: end, value } = ta
    const lineStart = value.lastIndexOf('\n', start - 1) + 1
    const restore = (s, end2) => requestAnimationFrame(() => ta.setSelectionRange(s, end2))

    if (!e.shiftKey && start === end) {
      setText(value.slice(0, start) + INDENT + value.slice(end))
      setDirty(true)
      restore(start + INDENT.length, start + INDENT.length)
      return
    }

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

  async function importFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    try {
      const raw = await file.text()
      const doc = JSON.parse(raw)
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
      // Honour the document's own updated_by / note instead of hardcoding — so a
      // "updated_by": "sarthak" in the JSON is what the store (and the meta) shows.
      const r = await putPolicy(doc, doc.updated_by || 'awcp-ui', doc.note || '')
      setDirty(false)
      setMsg({ tone: 'green', text: `✓ saved v${r.version} (${r.enabled ? 'active' : 'inert'})` })
      refresh()
    } catch (e) {
      setMsg({ tone: 'red', text: `✗ ${e.message}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <Panel>
        <div className="space-y-4 px-5 py-4">
          {/* View toggle (the "slider"): Visual builder is the default; flip it to
              show the current policy as raw, editable JSON. Both edit one document. */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
              <LegendDot hex="#22c55e" label="allow" />
              <LegendDot hex="#f43f5e" label="deny" />
              <LegendDot hex={DEFAULT_HEX} label="default (slider decides)" />
              <span className="text-slate-300">·</span>
              <span>risk:</span>
              <LegendDot hex={TIER_HEX.low} label="low" />
              <LegendDot hex={TIER_HEX.medium} label="medium" />
              <LegendDot hex={TIER_HEX.high} label="high" />
              <LegendDot hex={TIER_HEX.severe} label="severe" />
            </div>
            <div className="flex items-center gap-3">
              <span className="rounded-md border border-slate-300 bg-slate-50 px-2.5 py-1 font-mono text-[11px] text-slate-600 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {meta}
              </span>
              <ViewToggle view={view} setView={setView} />
            </div>
          </div>

          {view === 'builder' ? (
            <PolicyBuilder text={text} setText={setText} setDirty={setDirty} onGotoJson={() => setView('json')} />
          ) : (
            <>
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
            </>
          )}

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
            <button
              onClick={downloadTemplate}
              title="Download a documented blank policy JSON to fill in"
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
            >
              ↓ Blank template
            </button>
            {view === 'json' && (
              <>
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
              </>
            )}
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

// ── Visual builder ────────────────────────────────────────────────────────────
// A structured editor over the SAME policy document as the JSON view: it parses
// `text`, renders every agent + tool rule with a colour-coded risk slider and
// Allow/Deny checkboxes, and writes edits straight back into `text` (so Save and
// the JSON view stay in sync). Keys not shown here (skills, exclusion_list, note,
// version, _README…) are preserved untouched on every edit.
function PolicyBuilder({ text, setText, setDirty, onGotoJson }) {
  let policy = null
  let parseErr = false
  try {
    policy = text.trim() ? JSON.parse(text) : {}
  } catch {
    parseErr = true
  }
  if (parseErr || typeof policy !== 'object' || policy === null || Array.isArray(policy)) {
    return (
      <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        The policy isn’t valid JSON right now, so the builder can’t render it.{' '}
        <button onClick={onGotoJson} className="font-semibold underline">
          Switch to JSON
        </button>{' '}
        to fix it.
      </div>
    )
  }

  const write = (next) => {
    setText(JSON.stringify(next, null, 2))
    setDirty(true)
  }
  const update = (section, key, patch) => {
    const next = { ...policy, [section]: { ...(policy[section] || {}) } }
    const entry = { ...(next[section][key] || {}) }
    for (const [pk, pv] of Object.entries(patch)) {
      if (pv === undefined) delete entry[pk]
      else entry[pk] = pv
    }
    next[section][key] = entry
    write(next)
  }
  const remove = (section, key) => {
    const next = { ...policy, [section]: { ...(policy[section] || {}) } }
    delete next[section][key]
    write(next)
  }
  const add = (section, key) => {
    const k = (key || '').trim()
    if (!k) return
    const next = { ...policy, [section]: { ...(policy[section] || {}), [k]: (policy[section] || {})[k] || {} } }
    write(next)
  }

  // ── nested per-tool overrides, stored at agents.<agent>.tools.<toolKey> ───────
  // ONE agent can carry its own risk tier / active flag for a specific tool; a tool
  // the agent doesn't nest falls back to the overall tools.<tool> rule.
  const updateNested = (agentKey, toolKey, patch) => {
    const next = { ...policy, agents: { ...(policy.agents || {}) } }
    const agent = { ...(next.agents[agentKey] || {}) }
    const toolsMap = { ...(agent.tools || {}) }
    const entry = { ...(toolsMap[toolKey] || {}) }
    for (const [pk, pv] of Object.entries(patch)) {
      if (pv === undefined) delete entry[pk]
      else entry[pk] = pv
    }
    toolsMap[toolKey] = entry
    agent.tools = toolsMap
    next.agents[agentKey] = agent
    write(next)
  }
  const removeNested = (agentKey, toolKey) => {
    const next = { ...policy, agents: { ...(policy.agents || {}) } }
    const agent = { ...(next.agents[agentKey] || {}) }
    const toolsMap = { ...(agent.tools || {}) }
    delete toolsMap[toolKey]
    if (Object.keys(toolsMap).length) agent.tools = toolsMap
    else delete agent.tools // drop an empty map so the JSON stays clean
    next.agents[agentKey] = agent
    write(next)
  }
  const addNested = (agentKey, toolKey) => {
    const k = (toolKey || '').trim()
    if (!k) return
    const next = { ...policy, agents: { ...(policy.agents || {}) } }
    const agent = { ...(next.agents[agentKey] || {}) }
    const toolsMap = { ...(agent.tools || {}) }
    if (!toolsMap[k]) toolsMap[k] = {}
    agent.tools = toolsMap
    next.agents[agentKey] = agent
    write(next)
  }

  return (
    <div className="space-y-5">
      <BuilderSection
        title="Agents"
        accent="#6366f1"
        hint="recognition + tier · match by agent id / name (globs ok) · expand an agent for per-tool overrides"
        section="agents"
        entries={policy.agents || {}}
        tiers={AGENT_TIERS}
        update={update}
        remove={remove}
        add={add}
        nested={{ update: updateNested, remove: removeNested, add: addNested }}
      />
      <BuilderSection
        title="Tools"
        accent="#0ea5e9"
        hint="overall allow + tier · match by tool name (globs ok)"
        section="tools"
        entries={policy.tools || {}}
        tiers={TOOL_TIERS}
        update={update}
        remove={remove}
        add={add}
      />
    </div>
  )
}

function BuilderSection({ title, hint, section, entries, tiers, update, remove, add, accent, nested }) {
  const [newKey, setNewKey] = useState('')
  const keys = Object.keys(entries).filter((k) => !k.startsWith('_'))
  const submit = () => {
    add(section, newKey)
    setNewKey('')
  }
  return (
    <section
      className="overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900/40"
      style={{ borderTop: `3px solid ${accent}` }}
    >
      {/* Labeled section header — Agents vs Tools read as two distinct blocks. */}
      <header className="flex items-baseline gap-2 border-b border-slate-100 bg-slate-50/70 px-4 py-2.5 dark:border-slate-700 dark:bg-slate-800/40">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ background: accent }}
          aria-hidden="true"
        />
        <h3 className="text-lg font-bold uppercase tracking-wide text-brand-900 dark:text-slate-100">{title}</h3>
        <span className="text-[11px] text-slate-400">{hint}</span>
        <span className="ml-auto text-[11px] font-medium text-slate-400">
          {keys.length} rule{keys.length === 1 ? '' : 's'}
        </span>
      </header>
      <div className="space-y-2 px-4 py-3">
        {keys.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-300 px-3 py-3 text-xs text-slate-400">
            No {title.toLowerCase()} rules yet — add one below.
          </div>
        )}
        {keys.map((k) => (
          <EntryRow
            key={k}
            entry={entries[k] || {}}
            name={k}
            tiers={tiers}
            onChange={(patch) => update(section, k, patch)}
            onRemove={() => remove(section, k)}
            nested={
              nested
                ? {
                    // per-tool overrides nested under this agent (agents.<a>.tools.<t>);
                    // nested rows always use the TOOL tier vocabulary.
                    entries: (entries[k] && entries[k].tools) || {},
                    tiers: TOOL_TIERS,
                    update: (toolKey, patch) => nested.update(k, toolKey, patch),
                    remove: (toolKey) => nested.remove(k, toolKey),
                    add: (toolKey) => nested.add(k, toolKey),
                  }
                : null
            }
          />
        ))}
        <div className="mt-1 flex items-center gap-2">
          <input
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder={`add ${title.slice(0, -1).toLowerCase()} rule (e.g. ${section === 'tools' ? 'run_command' : 'agent-langgraph-*'})`}
            className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono text-xs text-brand-900 outline-none focus:border-brand-500"
          />
          <button
            onClick={submit}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50"
          >
            + Add
          </button>
        </div>
      </div>
    </section>
  )
}

// Fixed control-column widths so the risk slider, on/off and trailing buttons line up
// across EVERY row regardless of name length or the per-tool badge — shared by the
// top-level rows and the nested per-tool rows so both grids align.
const COL_RISK = 'flex w-[15rem] shrink-0 items-center gap-2'
const COL_ONOFF = 'w-[10rem] shrink-0' // fits "Active / Inactive" without wrapping
const COL_TRAIL = 'w-[6.5rem] shrink-0' // per-tool button / spacer, kept constant width

// One top-level rule row (agents or tools). Writes `allow`; agent rows also expose the
// per-tool expander.
function EntryRow({ entry, name, tiers, onChange, onRemove, nested }) {
  const risk = entry.risk && tiers.includes(entry.risk) ? entry.risk : 'default'
  const allow = entry.allow === true ? true : entry.allow === false ? false : undefined
  // Left accent shows the effective decision at a glance: allow=green, deny=red, else
  // the risk tier's colour (slate for "default / no opinion").
  const accent = allow === true ? '#22c55e' : allow === false ? '#f43f5e' : tierHex(risk)
  const overrideKeys = nested ? Object.keys(nested.entries).filter((k) => !k.startsWith('_')) : []
  const [open, setOpen] = useState(overrideKeys.length > 0)
  return (
    <div
      className="rounded-lg border border-slate-200 bg-white dark:border-slate-700"
      style={{ borderLeft: `4px solid ${accent}` }}
    >
      <div className="flex items-center gap-x-4 py-2.5 pl-3 pr-3">
        <div className="min-w-[8rem] flex-1">
          <div className="truncate font-mono text-sm text-brand-900">{name}</div>
        </div>

        <div className={COL_RISK}>
          <span className="w-9 text-[10px] font-semibold uppercase tracking-wide text-slate-400">risk</span>
          <TierSlider
            options={['default', ...tiers]}
            value={risk}
            onChange={(v) => onChange({ risk: v === 'default' ? undefined : v })}
          />
        </div>

        <div className={COL_ONOFF}>
          <OnOff mode="allow" value={allow} onChange={(v) => onChange({ allow: v })} />
        </div>

        {nested ? (
          <button
            onClick={() => setOpen((o) => !o)}
            title="Per-tool overrides for this agent"
            className={`flex ${COL_TRAIL} items-center justify-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition ${
              overrideKeys.length
                ? 'bg-sky-50 text-sky-700 hover:bg-sky-100 dark:bg-sky-500/15 dark:text-sky-300'
                : 'text-slate-400 hover:bg-slate-50 hover:text-slate-600'
            }`}
          >
            <span className={`transition ${open ? 'rotate-90' : ''}`}>▸</span>
            per-tool
            {overrideKeys.length > 0 && (
              <span className="rounded-full bg-sky-600 px-1.5 text-[10px] font-semibold text-white">
                {overrideKeys.length}
              </span>
            )}
          </button>
        ) : (
          <span className={COL_TRAIL} aria-hidden="true" />
        )}

        <button
          onClick={onRemove}
          title="Remove rule"
          aria-label="Remove rule"
          className="w-5 shrink-0 text-slate-300 transition hover:text-rose-500"
        >
          ✕
        </button>
      </div>

      {nested && open && <NestedToolOverrides nested={nested} overrideKeys={overrideKeys} />}
    </div>
  )
}

// Per-tool overrides nested under one agent — all rows in ONE bounding box (not a box
// each) for readability. Each row sets a risk tier / active flag for a specific tool the
// agent calls; a tool the agent doesn't list falls back to the overall Tools rule.
function NestedToolOverrides({ nested, overrideKeys }) {
  const [newKey, setNewKey] = useState('')
  const submit = () => {
    nested.add(newKey)
    setNewKey('')
  }
  return (
    <div className="border-t border-dashed border-slate-200 bg-slate-50/60 px-3 py-2.5 dark:border-slate-700 dark:bg-slate-800/30">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] text-slate-500">
        <span className="font-semibold uppercase tracking-wide">Per-tool overrides</span>
        <span className="text-slate-400">this agent’s own tier / active for a tool — else the overall Tools rule</span>
      </div>
      {overrideKeys.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 px-3 py-2 text-[11px] text-slate-400">
          No per-tool overrides — this agent uses the overall Tools rules.
        </div>
      ) : (
        <div className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white dark:divide-slate-700 dark:border-slate-700 dark:bg-slate-900/40">
          {overrideKeys.map((tk) => (
            <NestedToolRow
              key={tk}
              entry={nested.entries[tk] || {}}
              name={tk}
              tiers={nested.tiers}
              onChange={(patch) => nested.update(tk, patch)}
              onRemove={() => nested.remove(tk)}
            />
          ))}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2">
        <input
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="tool name (e.g. run_command)"
          className="flex-1 rounded-md border border-slate-300 bg-white px-2.5 py-1 font-mono text-[11px] text-brand-900 outline-none focus:border-brand-500"
        />
        <button
          onClick={submit}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-[11px] font-medium text-slate-600 transition hover:bg-slate-50"
        >
          + Add tool
        </button>
      </div>
    </div>
  )
}

// A single per-tool override row INSIDE the shared bounding box — no per-row border box,
// just a thin colour accent + divider. Writes `active` (Active/Inactive). Same fixed
// control columns as EntryRow so the sliders line up.
function NestedToolRow({ entry, name, tiers, onChange, onRemove }) {
  const risk = entry.risk && tiers.includes(entry.risk) ? entry.risk : 'default'
  const active = entry.active === true ? true : entry.active === false ? false : undefined
  const accent = active === true ? '#22c55e' : active === false ? '#f43f5e' : tierHex(risk)
  return (
    <div className="flex items-center gap-x-4 py-2 pl-3 pr-3" style={{ borderLeft: `3px solid ${accent}` }}>
      <div className="min-w-[8rem] flex-1">
        <div className="truncate font-mono text-[13px] text-brand-900">{name}</div>
      </div>
      <div className={COL_RISK}>
        <span className="w-9 text-[10px] font-semibold uppercase tracking-wide text-slate-400">risk</span>
        <TierSlider
          options={['default', ...tiers]}
          value={risk}
          onChange={(v) => onChange({ risk: v === 'default' ? undefined : v })}
        />
      </div>
      <div className={COL_ONOFF}>
        <OnOff mode="active" value={active} onChange={(v) => onChange({ active: v })} />
      </div>
      <button
        onClick={onRemove}
        title="Remove override"
        aria-label="Remove override"
        className="w-5 shrink-0 text-slate-300 transition hover:text-rose-500"
      >
        ✕
      </button>
    </div>
  )
}

// A colour-coded risk-tier slider (reuses the .risk-slider styling from index.css).
// Leftmost stop is "default" (no override → slate); the rest set an explicit tier.
function TierSlider({ options, value, onChange }) {
  const idx = Math.max(0, options.indexOf(value))
  const cur = options[idx]
  const hex = cur === 'default' ? DEFAULT_HEX : tierHex(cur)
  const pct = (idx / Math.max(1, options.length - 1)) * 100
  return (
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={0}
        max={options.length - 1}
        step={1}
        value={idx}
        aria-label="risk tier"
        onChange={(e) => onChange(options[Number(e.target.value)])}
        className="risk-slider w-28"
        style={{
          background: `linear-gradient(90deg, ${hex} 0%, ${hex} ${pct}%, #ffffff ${pct}%, #ffffff 100%)`,
          '--thumb-color': hex,
        }}
      />
      <span className="w-16 text-xs font-semibold" style={{ color: hex }}>
        {cur}
      </span>
    </div>
  )
}

// Explicit on/off checkboxes — mutually exclusive; neither ticked = default (defer to
// the slider). `mode` picks the labels: 'allow' → Allow / Deny (top-level tools+agents,
// writes `allow`); 'active' → Active / Inactive (nested per-tool, writes `active`).
// Green for on, red for off.
function OnOff({ value, onChange, mode = 'allow' }) {
  const [onLabel, offLabel] = mode === 'active' ? ['Active', 'Inactive'] : ['Allow', 'Deny']
  return (
    <div className="flex items-center gap-3 whitespace-nowrap text-xs">
      <label
        className={`flex cursor-pointer items-center gap-1.5 ${
          value === true ? 'font-semibold text-brand-700' : 'text-slate-500'
        }`}
      >
        <input
          type="checkbox"
          checked={value === true}
          onChange={() => onChange(value === true ? undefined : true)}
          className="h-3.5 w-3.5 accent-[#22c55e]"
        />
        {onLabel}
      </label>
      <label
        className={`flex cursor-pointer items-center gap-1.5 ${
          value === false ? 'font-semibold text-rose-600' : 'text-slate-500'
        }`}
      >
        <input
          type="checkbox"
          checked={value === false}
          onChange={() => onChange(value === false ? undefined : false)}
          className="h-3.5 w-3.5 accent-[#f43f5e]"
        />
        {offLabel}
      </label>
    </div>
  )
}

function ViewToggle({ view, setView }) {
  const json = view === 'json'
  return (
    <button
      onClick={() => setView(json ? 'builder' : 'json')}
      role="switch"
      aria-checked={json}
      title="Toggle between the visual builder and the raw JSON"
      className="flex shrink-0 items-center gap-2 text-xs font-medium text-slate-500"
    >
      <span className={!json ? 'font-semibold text-brand-700' : ''}>Visual</span>
      <span
        className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${
          json ? 'bg-brand-600' : 'bg-slate-300'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${
            json ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </span>
      <span className={json ? 'font-semibold text-brand-700' : ''}>JSON</span>
    </button>
  )
}

function LegendDot({ hex, label }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="h-2 w-2 rounded-full" style={{ background: hex }} />
      {label}
    </span>
  )
}
