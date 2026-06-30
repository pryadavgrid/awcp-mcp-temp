import { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar.jsx'
import { Icon } from './components/Icons.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Radar from './pages/Radar.jsx'
import Approvals from './pages/Approvals.jsx'
import Workflows from './pages/Workflows.jsx'
import ContextGraph from './pages/ContextGraph.jsx'
import TokenMonitor from './pages/TokenMonitor.jsx'
import Hooks from './pages/Hooks.jsx'
import Policy from './pages/Policy.jsx'
import Sandbox from './pages/Sandbox.jsx'
import { usePoll } from './hooks/usePoll.js'
import { useTheme } from './hooks/useTheme.js'
import { useMediaQuery } from './hooks/useMediaQuery.js'
import { getHealth, getApprovals } from './api.js'
import { API_BASE } from './config.js'

const SIDEBAR_KEY = 'awcp-sidebar-collapsed'

// Each page carries a big heading + a soft subheading, mirroring the reference
// dashboard's "Dashboard / Plan, prioritize…" header pattern.
const PAGES_META = {
  dashboard: {
    title: 'Dashboard',
    subtitle: 'Monitor, govern, and steer your agent workforce at a glance.',
  },
  radar: {
    title: 'Radar',
    subtitle: 'Every agentic environment the radar has scanned or that self-registered.',
  },
  approvals: {
    title: 'Approvals',
    subtitle: 'Review write actions agents paused on — approve to proceed, deny to block.',
  },
  workflow: {
    title: 'Workflow',
    subtitle: 'Live Temporal status for every onboarding & task-execution run.',
  },
  context: {
    title: 'Context Graph',
    subtitle: 'Every governed step recorded as a tamper-chained node.',
  },
  tokens: {
    title: 'Token Monitor',
    subtitle: 'Per-agent token usage, budget state, and cost over the sliding window.',
  },
  hooks: {
    title: 'Agent Hooks',
    subtitle: 'Lifecycle hooks fired by the control plane as agents run.',
  },
  policy: {
    title: 'Operator Policy',
    subtitle: 'Operator-authored allow / risk rules for detected agents and tools.',
  },
  sandbox: {
    title: 'Sandbox',
    subtitle: 'Isolated container execution and the live tool-call timeline.',
  },
}

// Valid page ids are exactly the nav entries — derived, not hardcoded twice.
const PAGES = new Set(Object.keys(PAGES_META))

// The current page lives in the URL hash (e.g. #radar). Reading it on load is
// what makes a refresh stay on the same page; an unknown/empty hash → dashboard.
const pageFromHash = () => {
  const h = (window.location.hash || '').replace(/^#\/?/, '')
  return PAGES.has(h) ? h : 'dashboard'
}

export default function App() {
  const [active, setActive] = useState(pageFromHash)
  const { isDark, toggle: toggleTheme } = useTheme()

  // At lg+ the sidebar is a static column; below that it becomes an off-canvas
  // drawer opened by the header hamburger.
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const [mobileOpen, setMobileOpen] = useState(false)

  // Sidebar collapse (icon-rail) state, persisted so it survives reloads.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === '1'
    } catch {
      return false
    }
  })
  const toggleSidebar = () =>
    setCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem(SIDEBAR_KEY, next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })

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

  // Nav selection: close the mobile drawer, then navigate.
  const handleSelect = (id) => {
    setMobileOpen(false)
    navigate(id)
  }

  // Icon-rail collapse only applies on desktop; the mobile drawer is always full.
  const effectiveCollapsed = collapsed && isDesktop

  const meta = PAGES_META[active] || PAGES_META.dashboard

  return (
    <div className="flex h-full overflow-x-hidden bg-[#f3f5f3]">
      {/* Backdrop behind the mobile drawer (tap to close). */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-slate-900/50 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      <Sidebar
        active={active}
        onSelect={handleSelect}
        health={health}
        approvalsCount={pendingCount}
        collapsed={effectiveCollapsed}
        onToggleCollapse={toggleSidebar}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />

      {toast && (
        <button
          onClick={() => {
            setToast(null)
            navigate('approvals')
          }}
          className="fixed right-4 top-4 z-[60] flex max-w-[calc(100vw-2rem)] items-center gap-3 rounded-2xl border border-brand-200 bg-white px-4 py-3 text-left shadow-card-hover transition hover:border-brand-300 sm:right-6 sm:top-6"
        >
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-600 text-sm font-bold text-white shadow-sm">
            {toast.count}
          </span>
          <div>
            <div className="text-sm font-semibold text-brand-900">{toast.text}</div>
            <div className="text-xs text-brand-600">Click to review the approvals queue →</div>
          </div>
        </button>
      )}

      <main className="flex min-w-0 flex-1 flex-col overflow-y-auto overflow-x-hidden">
        <div className="mx-auto w-full max-w-[1600px] space-y-5 px-3 py-4 sm:space-y-6 sm:px-6 sm:py-6">
          {/* ── Top bar: menu · search · status · notifications · operator ──── */}
          <header className="flex items-center justify-between gap-2 rounded-2xl border border-slate-100 bg-white px-3 py-2.5 shadow-card sm:gap-3 sm:px-4 sm:py-3">
            <button
              onClick={() => setMobileOpen(true)}
              title="Open menu"
              aria-label="Open menu"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-slate-100 bg-white text-slate-500 transition hover:border-brand-200 hover:text-brand-600 lg:hidden"
            >
              <Icon name="menu" className="h-5 w-5" />
            </button>
            <label className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl bg-slate-100/80 px-3.5 py-2.5 text-sm text-slate-500 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-brand-500/30 sm:max-w-md">
              <Icon name="search" className="h-4 w-4 shrink-0 text-slate-400" strokeWidth={2} />
              <input
                type="text"
                placeholder="Search agents, workflows, tools…"
                className="w-full bg-transparent placeholder:text-slate-400 focus:outline-none"
              />
              <kbd className="hidden rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] font-semibold text-slate-400 sm:inline">
                ⌘K
              </kbd>
            </label>

            <div className="flex items-center gap-1.5 sm:gap-3">
              <span className="hidden text-[11px] text-slate-400 lg:inline">{API_BASE}</span>
              <span
                className={`hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset sm:flex ${
                  error
                    ? 'bg-rose-50 text-rose-700 ring-rose-600/20'
                    : 'bg-brand-50 text-brand-700 ring-brand-600/20'
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${error ? 'bg-rose-500' : 'animate-pulse bg-brand-500'}`}
                />
                {error ? 'gateway unreachable' : 'live'}
              </span>

              <button
                onClick={toggleTheme}
                title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
                aria-label="Toggle theme"
                className="grid h-10 w-10 place-items-center rounded-xl border border-slate-100 bg-white text-slate-500 transition hover:border-brand-200 hover:text-brand-600"
              >
                <Icon name={isDark ? 'sun' : 'moon'} className="h-5 w-5" />
              </button>

              <button
                onClick={() => navigate('approvals')}
                title="Pending approvals"
                className="relative grid h-10 w-10 place-items-center rounded-xl border border-slate-100 bg-white text-slate-500 transition hover:border-brand-200 hover:text-brand-600"
              >
                <Icon name="bell" className="h-5 w-5" />
                {pendingCount > 0 && (
                  <span className="absolute -right-1 -top-1 grid h-5 min-w-[20px] place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white ring-2 ring-white">
                    {pendingCount}
                  </span>
                )}
              </button>

              <div className="flex items-center gap-2.5 rounded-xl border border-slate-100 py-1.5 pl-1.5 pr-3">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-800 text-xs font-bold text-white">
                  AW
                </span>
                <div className="hidden leading-tight sm:block">
                  <div className="text-sm font-semibold text-brand-900">Operator</div>
                  <div className="text-[11px] text-slate-400">Control Plane</div>
                </div>
              </div>
            </div>
          </header>

          {/* ── Page heading + subheading ──────────────────────────────────── */}
          <div className="flex flex-wrap items-end justify-between gap-3 pl-1 pt-1">
            <div>
              <h1 className="text-2xl font-extrabold leading-tight tracking-tight text-brand-900 sm:text-[28px]">
                {meta.title}
              </h1>
              <p className="mt-1 text-sm text-slate-400">{meta.subtitle}</p>
            </div>
          </div>

          {error && (
            <div className="break-words rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              Cannot reach the gateway at{' '}
              <span className="break-all font-mono">{API_BASE}</span> — {error}. Make sure it is
              running (e.g. <span className="break-all font-mono">bash scripts/run_everything.sh</span>
              ).
            </div>
          )}

          {active === 'dashboard' && <Dashboard onNavigate={navigate} />}
          {active === 'radar' && <Radar />}
          {active === 'approvals' && <Approvals />}
          {active === 'workflow' && <Workflows />}
          {active === 'context' && <ContextGraph />}
          {active === 'tokens' && <TokenMonitor />}
          {active === 'hooks' && <Hooks />}
          {active === 'policy' && <Policy />}
          {active === 'sandbox' && <Sandbox />}
        </div>
      </main>
    </div>
  )
}
