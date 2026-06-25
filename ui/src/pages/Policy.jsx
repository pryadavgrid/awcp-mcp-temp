import { useEffect, useRef, useState } from 'react'
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

export default function Policy() {
  const { data, loading, refresh } = usePoll(getPolicy, [])
  const { data: agentsData } = usePoll(getAgents, [])
  const agents = agentsData || []

  const [text, setText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState(null) // { tone:'green'|'red', text }
  const [saving, setSaving] = useState(false)
  const fileRef = useRef(null)

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
      setMsg({ tone: 'red', text: `invalid JSON: ${e.message}` })
    }
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
      setMsg({ tone: 'red', text: `import failed (${file.name}): ${err.message}` })
    }
  }

  async function save() {
    let doc
    try {
      doc = JSON.parse(text)
    } catch (e) {
      setMsg({ tone: 'red', text: `invalid JSON: ${e.message}` })
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
        subtitle="Name which detected agents are recognised (allowed) and at what risk tier — same for tools. The OPA agent assigns a baseline tier first; this policy is applied after, as the operator override. Stored in Postgres."
        right={<span className="text-xs text-slate-500">{meta}</span>}
      >
        <div className="space-y-3 px-5 py-4">
          <p className="text-xs leading-relaxed text-slate-500">
            Detection is unchanged — every process is still auto-detected. <b>Default check:</b> the{' '}
            <b>slider</b> on Tool Risk Tiers (“allowed risk level”) — an agent/tool is allowed when its risk tier is{' '}
            <i>below</i> the slider, denied at or above it. <b>Override:</b> a rule with{' '}
            <code className="rounded bg-slate-100 px-1 font-mono text-[11px]">allow:true</code> is always allowed and{' '}
            <code className="rounded bg-slate-100 px-1 font-mono text-[11px]">allow:false</code> always denied; omit{' '}
            <code className="rounded bg-slate-100 px-1 font-mono text-[11px]">allow</code> to let the slider decide.{' '}
            <code className="rounded bg-slate-100 px-1 font-mono text-[11px]">risk</code> relabels the tier compared to the slider.
            Agent keys match an <code className="rounded bg-slate-100 px-1 font-mono text-[11px]">id</code>/
            <code className="rounded bg-slate-100 px-1 font-mono text-[11px]">name</code> (globs ok); tool keys match the tool name.
            Agent risk ∈ low|medium|high; tool risk ∈ low|medium|high|severe.
          </p>

          <textarea
            value={text}
            spellCheck={false}
            onChange={(e) => {
              setText(e.target.value)
              setDirty(true)
            }}
            placeholder={loading ? 'Loading…' : '{ "defaults": {...}, "agents": {...}, "tools": {...} }'}
            className="h-80 w-full resize-y rounded-lg border border-slate-300 bg-slate-50 p-3 font-mono text-[12.5px] leading-relaxed text-slate-800 outline-none focus:border-brand-500 focus:bg-white"
          />

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
                className={`font-mono text-xs ${msg.tone === 'green' ? 'text-emerald-600' : 'text-rose-600'}`}
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
        <Table columns={['Name', 'Status', 'Recognised', 'Risk (authoritative)']}>
          {agents.length === 0 ? (
            <EmptyRow colSpan={4}>No agents detected yet.</EmptyRow>
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
              </tr>
            ))
          )}
        </Table>
      </Panel>
    </div>
  )
}
