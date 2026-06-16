import { useEffect, useState } from 'react'
import {
  API_BASE,
  listAgents,
  getUsage,
  getBudgets,
  getRegistryAgents,
  submitTask,
  getStatus,
  approveTask,
} from './api.js'
import AgentPicker from './components/AgentPicker.jsx'
import Timeline from './components/Timeline.jsx'
import ResultPanel from './components/ResultPanel.jsx'
import TokenBar from './components/TokenBar.jsx'

const TERMINAL = new Set(['done', 'failed', 'blocked'])

// Resolve an agent's tokens-per-window budget, mirroring laminar's precedence
// (operator override → declared token_budget → risk tier → system default) so a
// registered agent shows its budget bar even before it has spent a single token.
function resolveBudget(entry, budgets) {
  if (!entry) return 0
  const ov = (budgets.overrides || {})[entry.id]
  if (ov && ov > 0) return ov
  if (entry.token_budget && entry.token_budget > 0) return entry.token_budget
  const tier = (budgets.risk_defaults || {})[entry.risk]
  return tier || budgets.system_default || 0
}

// Build the token-bar view for the selected agent from the best data available:
//   • a live /laminar/usage row → real used / budget / state;
//   • else, if the agent has a control-plane identity (agent_id) but hasn't spent
//     yet → 0 used against its resolved budget (a matching registry entry's
//     declared budget, else the system default laminar would assign it);
//   • else (no agent_id yet — agent not started/registered) → a placeholder.
function tokenInfoFor(selected, usage, regAgents, budgets) {
  if (!selected) return { info: null, pending: false }
  const aid = selected.agent_id
  if (!aid) return { info: null, pending: true }
  const row = usage.find((u) => u.agent_id && u.agent_id === aid)
  if (row && row.budget) return { info: row, pending: false }
  const entry = regAgents.find((r) => r.id === aid)
  const budget = entry ? resolveBudget(entry, budgets) : budgets.system_default || 0
  return {
    info: {
      agent_id: aid,
      budget: { used_tokens: 0, budget_tokens: budget, ratio: 0, state: 'ok' },
      window: { total_tokens: 0, calls: 0 },
    },
    pending: false,
  }
}

export default function App() {
  const [agents, setAgents] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [prompt, setPrompt] = useState('')
  const [task, setTask] = useState(null) // /user/submit response
  const [status, setStatus] = useState(null) // latest /user/status
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [backendOk, setBackendOk] = useState(null)
  const [usage, setUsage] = useState([]) // /laminar/usage rows (agents that spent)
  const [budgets, setBudgets] = useState({}) // /laminar/budgets policy
  const [regAgents, setRegAgents] = useState([]) // /agents registry entries

  const selected = agents.find((a) => a.id === selectedId) || null
  const acceptsFiles = !!(selected && selected.accepts_files)
  const running = status && !TERMINAL.has(status.status)
  // token view for the selected agent: a live usage row, or its resolved budget
  // (0 used) if registered but not spent yet, or a "not started" placeholder.
  const { info: myUsage, pending: tokenPending } = tokenInfoFor(
    selected,
    usage,
    regAgents,
    budgets,
  )

  // Load agents on mount, then refresh periodically so running-state stays live.
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const a = await listAgents()
        if (!alive) return
        setAgents(a)
        setBackendOk(true)
        setSelectedId((prev) => prev || (a[0] && a[0].id) || '')
      } catch {
        if (alive) setBackendOk(false)
      }
      try {
        const [u, b, ra] = await Promise.all([
          getUsage().catch(() => null),
          getBudgets().catch(() => null),
          getRegistryAgents().catch(() => null),
        ])
        if (!alive) return
        if (u) setUsage(u)
        if (b) setBudgets(b)
        if (ra) setRegAgents(ra)
      } catch {
        /* token monitor is optional — leave usage/budgets as-is */
      }
    }
    load()
    const t = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  // While a task is active, poll its status + timeline ~1/s.
  useEffect(() => {
    if (!task) return
    let alive = true
    let timer
    const poll = async () => {
      try {
        const s = await getStatus(task.agent, task.task_id, task.workflow_id)
        if (!alive) return
        setStatus(s)
        try {
          const u = await getUsage()
          if (alive) setUsage(u || [])
        } catch {
          /* token monitor optional */
        }
        if (TERMINAL.has(s.status)) return // settled — stop polling
      } catch {
        /* transient — keep trying */
      }
      if (alive) timer = setTimeout(poll, 1200)
    }
    poll()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [task])

  // Switching agents drops any attached file — paths are per-agent.
  useEffect(() => {
    setAttached(null)
    setUploading(false)
    setDragOver(false)
  }, [selectedId])

  const onPickFile = async (f) => {
    if (!f || !selectedId) return
    setError('')
    setUploading(true)
    try {
      const res = await uploadFile(selectedId, f)
      setAttached(res) // { path, filename, size }
    } catch (e) {
      setError(`Upload failed: ${String(e.message || e)}`)
      setAttached(null)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const onRun = async () => {
    setError('')
    const text = prompt.trim()
    if (!selectedId) return setError('Select an agent first.')
    // A file agent can run on the attachment alone, with a sensible default goal.
    if (!text && !(acceptsFiles && attached)) return setError('Enter a prompt.')
    const goal = acceptsFiles && attached
      ? `${text || 'Identify this file and tell me what it is and what it contains.'}\n\nFILE_PATH: ${attached.path}`
      : text
    setBusy(true)
    setStatus(null)
    setTask(null)
    try {
      const t = await submitTask(selectedId, goal)
      setTask(t)
      setStatus({
        status: t.status || 'queued',
        timeline: [],
        result: '',
        tools_used: [],
        steps: [],
        temporal_url: t.temporal_url,
      })
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const onApprove = async (decision) => {
    if (!task) return
    try {
      await approveTask(task.agent, task.task_id, decision)
    } catch (e) {
      setError(String(e.message || e))
    }
  }

  const onReset = () => {
    setTask(null)
    setStatus(null)
    setError('')
    setPrompt('')
    setAttached(null)
  }

  const fmtSize = (n) =>
    n == null ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">▰</span>
          <div>
            <h1>AWCP Agent Console</h1>
            <div className="sub">select an agent · run a prompt · watch every step live</div>
          </div>
        </div>
        <div className="conn">
          <span className={`dot ${backendOk ? 'on' : backendOk === false ? 'off' : ''}`} />
          <span className="mono">{API_BASE}</span>
        </div>
      </header>

      <main className="main">
        {/* Composer */}
        <section className="composer card">
          <AgentPicker
            agents={agents}
            selectedId={selectedId}
            selected={selected}
            onSelect={setSelectedId}
            disabled={busy || running}
          />

          <TokenBar
            usage={myUsage}
            pending={tokenPending}
            agentName={selected ? selected.name || selected.id : ''}
          />

          {selected && (selected.examples || []).length > 0 && (
            <div className="examples">
              {selected.examples.map((ex, i) => (
                <button
                  key={i}
                  className="ex"
                  disabled={busy || running}
                  onClick={() => setPrompt(ex)}
                >
                  {ex}
                </button>
              ))}
            </div>
          )}

          {acceptsFiles && (
            <div
              className={`filezone${dragOver ? ' drag' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                if (!busy && !running && !uploading) setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragOver(false)
                if (busy || running || uploading) return
                const f = e.dataTransfer.files && e.dataTransfer.files[0]
                if (f) onPickFile(f)
              }}
            >
              <input
                ref={fileInput}
                type="file"
                hidden
                onChange={(e) => onPickFile(e.target.files[0])}
              />
              <button
                className="btn ghost"
                onClick={() => fileInput.current && fileInput.current.click()}
                disabled={busy || running || uploading}
              >
                {uploading ? 'Uploading…' : '📎 Attach file'}
              </button>
              {attached ? (
                <span className="filechip">
                  <span className="fname">{attached.filename}</span>
                  <span className="fsize">{fmtSize(attached.size)}</span>
                  <button
                    className="file-x"
                    title="Remove file"
                    onClick={() => setAttached(null)}
                    disabled={busy || running}
                  >
                    ✕
                  </button>
                </span>
              ) : (
                <span className="hint">or drag &amp; drop a file — then optionally add a prompt</span>
              )}
            </div>
          )}

          <textarea
            className="prompt"
            placeholder={
              acceptsFiles
                ? 'Attach a file, then describe what you want to know about it (optional)…'
                : 'Type a prompt / goal for the selected agent…'
            }
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={busy || running}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onRun()
            }}
          />

          <div className="composer-row">
            <span className="hint">⌘/Ctrl + Enter to run</span>
            <div className="spacer" />
            {(task || status) && (
              <button className="btn ghost" onClick={onReset} disabled={busy}>
                New run
              </button>
            )}
            <button
              className="btn primary"
              onClick={onRun}
              disabled={busy || running || uploading || !selectedId}
            >
              {busy ? 'Submitting…' : running ? 'Running…' : 'Run ▸'}
            </button>
          </div>

          {error && <div className="err inline">{error}</div>}
          {backendOk === false && (
            <div className="err inline">
              Can’t reach the gateway at {API_BASE}. Is it running? (set VITE_API_BASE to change.)
            </div>
          )}
        </section>

        {/* Run view */}
        {task && (
          <section className="run">
            <div className="run-head card">
              <div className="run-goal">
                <span className="lbl">Prompt</span>
                <div className="goal-text">{prompt || '—'}</div>
              </div>
              <div className={`pill p-${(status && status.status) || 'queued'}`}>
                <span className="pdot" />
                {(status && status.status) || 'queued'}
              </div>
            </div>

            <div className="run-cols">
              <div className="col card">
                <div className="col-title">Status</div>
                <Timeline items={status && status.timeline} status={status && status.status} />
              </div>
              <div className="col card">
                <div className="col-title">Result</div>
                <ResultPanel status={status} onApprove={onApprove} />
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}
