import { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Radar from './pages/Radar.jsx'
import Approvals from './pages/Approvals.jsx'
import Workflows from './pages/Workflows.jsx'
import TokenMonitor from './pages/TokenMonitor.jsx'
import Hooks from './pages/Hooks.jsx'
import Policy from './pages/Policy.jsx'
import Sandbox from './pages/Sandbox.jsx'
import { usePoll } from './hooks/usePoll.js'
import { getHealth, getApprovals } from './api.js'
import { API_BASE } from './config.js'

const TITLES = {
  dashboard: 'Dashboard',
  radar: 'Radar',
  approvals: 'Approvals',
  workflow: 'Workflow',
  tokens: 'Token Monitor',
  hooks: 'Agent Hooks',
  policy: 'Operator Policy',
  sandbox: 'Sandbox',
}

// Valid page ids are exactly the nav entries — derived, not hardcoded twice.
const PAGES = new Set(Object.keys(TITLES))

// The current page lives in the URL hash (e.g. #radar). Reading it on load is
// what makes a refresh stay on the same page; an unknown/empty hash → dashboard.
const pageFromHash = () => {
  const h = (window.location.hash || '').replace(/^#\/?/, '')
  return PAGES.has(h) ? h : 'dashboard'
}

export default function App() {
  const [active, setActive] = useState(pageFromHash)
  // One shared health poll drives the header status + sidebar connection dots.
  const { data: health, error } = usePoll(getHealth, [])

  // Live pending-approvals poll → the sidebar count badge + the "new request"
  // toast. getApprovals('pending') returns the array of paused write actions.
  const { data: pendingData } = usePoll(() => getApprovals('pending', 100), [])
  const pendingCount = Array.isArray(pendingData) ? pendingData.length : 0

  // Fire a toast only when the queue GROWS (a genuinely new request), never on
  // the first load or when the count drops because the operator just decided one.
  const prevCount = useRef(null)
  const [toast, setToast] = useState(null)
  useEffect(() => {
    const prev = prevCount.current
    if (prev !== null && pendingCount > prev) {
      const added = pendingCount - prev
      setToast({
        text: added === 1 ? 'New write approval request' : `${added} new write approval requests`,
        count: pendingCount,
      })
    }
    prevCount.current = pendingCount
  }, [pendingCount])

  // Auto-dismiss the toast after a few seconds.
  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(null), 6000)
    return () => clearTimeout(id)
  }, [toast])

  // Keep the page in the URL hash so (a) a refresh stays on the same page and
  // (b) the browser Back/Forward buttons move between pages. The hash is the
  // single source of truth: a nav click sets the hash, and the hashchange
  // listener — fired by clicks AND by Back/Forward — updates the rendered page.
  useEffect(() => {
    if (!window.location.hash) {
      window.history.replaceState(null, '', `#${active}`)
    }
    const onHashChange = () => setActive(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Navigate by setting the hash (pushes a history entry so Back/Forward works);
  // the hashchange listener then updates `active`.
  const navigate = (id) => {
    if (id === active) return
    window.location.hash = id
  }

  return (
    <div className="flex h-full">
      <Sidebar active={active} onSelect={navigate} health={health} approvalsCount={pendingCount} />

      {toast && (
        <button
          onClick={() => {
            setToast(null)
            navigate('approvals')
          }}
          className="fixed right-6 top-6 z-50 flex items-center gap-3 rounded-xl border border-brand-200 bg-white px-4 py-3 text-left shadow-lg ring-1 ring-brand-500/15 transition hover:border-brand-300 hover:shadow-xl"
        >
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-500 text-sm font-bold text-white shadow-sm">
            {toast.count}
          </span>
          <div>
            <div className="text-sm font-semibold text-brand-900">{toast.text}</div>
            <div className="text-xs text-brand-600">Click to review the approvals queue →</div>
          </div>
        </button>
      )}

      <main className="flex flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4">
          <div>
            <h1 className="text-lg font-bold text-brand-900">AWCP Dashboard</h1>
            <p className="text-xs text-slate-500">{TITLES[active]}</p>
          </div>
          <div className="flex items-center gap-3 text-xs">
            <span className="hidden text-slate-400 sm:inline">{API_BASE}</span>
            <span
              className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 ring-1 ring-inset ${
                error
                  ? 'bg-rose-100 text-rose-700 ring-rose-600/30'
                  : 'bg-brand-100 text-brand-700 ring-brand-600/25'
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${error ? 'bg-rose-500' : 'bg-brand-500 animate-pulse'}`}
              />
              {error ? 'gateway unreachable' : 'live'}
            </span>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-6">
          {error && (
            <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              Cannot reach the gateway at <span className="font-mono">{API_BASE}</span> — {error}.
              Make sure it is running (e.g. <span className="font-mono">bash scripts/run_everything.sh</span>).
            </div>
          )}
          {active === 'dashboard' && <Dashboard />}
          {active === 'radar' && <Radar />}
          {active === 'approvals' && <Approvals />}
          {active === 'workflow' && <Workflows />}
          {active === 'tokens' && <TokenMonitor />}
          {active === 'hooks' && <Hooks />}
          {active === 'policy' && <Policy />}
          {active === 'sandbox' && <Sandbox />}
        </div>
      </main>
    </div>
  )
}
