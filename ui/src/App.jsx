import { useEffect, useRef, useState } from 'react'
import {
  API_BASE,
  listAgents,
  getUsage,
  submitTask,
  getStatus,
  approveTask,
  uploadFile,
} from './api.js'
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
  const [attached, setAttached] = useState(null) // { path, filename, size } for file agents
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInput = useRef(null)

  const selected = agents.find((a) => a.id === selectedId) || null
  const acceptsFiles = !!(selected && selected.accepts_files)
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
