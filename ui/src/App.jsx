import { useEffect, useState } from 'react'
import { API_BASE, listAgents, getUsage, submitTask, getStatus, approveTask } from './api.js'
import AgentPicker from './components/AgentPicker.jsx'
import Timeline from './components/Timeline.jsx'
import ResultPanel from './components/ResultPanel.jsx'
import TokenBar from './components/TokenBar.jsx'

const TERMINAL = new Set(['done', 'failed', 'blocked'])

export default function App() {
  const [agents, setAgents] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [prompt, setPrompt] = useState('')
  const [task, setTask] = useState(null) // /user/submit response
  const [status, setStatus] = useState(null) // latest /user/status
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [backendOk, setBackendOk] = useState(null)
  const [usage, setUsage] = useState([]) // /laminar/usage rows (per agent)

  const selected = agents.find((a) => a.id === selectedId) || null
  const running = status && !TERMINAL.has(status.status)
  // token usage for the selected agent, matched on the radar/Temporal agent_id
  const myUsage =
    usage.find((u) => selected && u.agent_id && u.agent_id === selected.agent_id) || null

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
        const u = await getUsage()
        if (alive) setUsage(u || [])
      } catch {
        /* token monitor is optional — leave usage as-is */
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

  const onRun = async () => {
    setError('')
    const text = prompt.trim()
    if (!selectedId) return setError('Select an agent first.')
    if (!text) return setError('Enter a prompt.')
    setBusy(true)
    setStatus(null)
    setTask(null)
    try {
      const t = await submitTask(selectedId, text)
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
  }

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

          <TokenBar usage={myUsage} />

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

          <textarea
            className="prompt"
            placeholder="Type a prompt / goal for the selected agent…"
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
            <button className="btn primary" onClick={onRun} disabled={busy || running || !selectedId}>
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
                <div className="col-title">Timeline</div>
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
