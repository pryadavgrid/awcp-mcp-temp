import { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar.jsx'
import { Splash } from './components/Splash.jsx'
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
import { getUsername, logout } from './lib/auth'

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

// The current page lives in the URL PATH (e.g. /radar). Reading it on load is
// what makes a refresh stay on the same page; the root "/" or any unmatched route
// → dashboard. Deep-linking a path (e.g. refreshing /sandbox) relies on the
// dev/preview server's SPA fallback serving index.html — Vite's default
// appType:'spa' does this, so no extra config is needed.
const pageFromPath = () => {
  const p = (window.location.pathname || '/').replace(/^\/+/, '').replace(/\/+$/, '')
  return PAGES.has(p) ? p : 'dashboard'
}

export default function App() {
  const [active, setActive] = useState(pageFromPath)
  const { isDark, toggle: toggleTheme } = useTheme()

  // Boot splash: on a fresh page load, show the animated logo as a loading screen
  // for a short beat, then fade it out and reveal the dashboard (which mounts and
  // starts polling underneath the whole time). Only fires on a full page load.
  const [booting, setBooting] = useState(true)
  const [splashLeaving, setSplashLeaving] = useState(false)
  useEffect(() => {
    const SHOW_MS = 2000
    const FADE_MS = 500
    const t1 = setTimeout(() => setSplashLeaving(true), SHOW_MS)
    const t2 = setTimeout(() => setBooting(false), SHOW_MS + FADE_MS)
    return () => {
      clearTimeout(t1)
      clearTimeout(t2)
    }
  }, [])

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
  const pendingList = Array.isArray(pendingData) ? pendingData : []
  const pendingCount = pendingList.length

  // Notification dropdown (the header bell): opens on click; each item drills into
  // the Approvals page for that paused write action. Closes on outside click / Esc.
  const [notifOpen, setNotifOpen] = useState(false)
  const notifRef = useRef(null)

  // Signed-in operator (from the Keycloak token) + a menu to sign out.
  const username = getUsername() || 'Operator'
  const initials = (username.replace(/[^a-zA-Z0-9]/g, '').slice(0, 2) || 'AW').toUpperCase()
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const userRef = useRef(null)

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

  // Close the notification dropdown on an outside click or Escape.
  useEffect(() => {
    if (!notifOpen) return
    const onDown = (e) => {
      if (notifRef.current && !notifRef.current.contains(e.target)) setNotifOpen(false)
    }
    const onEsc = (e) => e.key === 'Escape' && setNotifOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onEsc)
    }
  }, [notifOpen])

  // Close the user menu on outside click / Esc (same pattern as the bell).
  useEffect(() => {
    if (!userMenuOpen) return
    const onDown = (e) => {
      if (userRef.current && !userRef.current.contains(e.target)) setUserMenuOpen(false)
    }
    const onEsc = (e) => e.key === 'Escape' && setUserMenuOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onEsc)
    }
  }, [userMenuOpen])

  // Keep the page in the URL path so (a) a refresh stays on the same page and
  // (b) the browser Back/Forward buttons move between pages. The path is the
  // single source of truth: a nav click pushes a history entry, and the popstate
  // listener — fired by Back/Forward — re-derives the rendered page.
  useEffect(() => {
    // Normalise the root "/" (or any unmatched route that fell back to dashboard)
    // to the canonical /dashboard, so the address bar always names the current
    // page. replaceState adds no extra history entry.
    if (pageFromPath() === 'dashboard' && window.location.pathname !== '/dashboard') {
      window.history.replaceState(null, '', '/dashboard')
    }
    const onPop = () => setActive(pageFromPath())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Navigate by pushing the new path (so Back/Forward works). pushState does NOT
  // fire popstate, so update the rendered page directly.
  const navigate = (id) => {
    if (id === active) return
    window.history.pushState(null, '', `/${id}`)
    setActive(id)
  }

  // Nav selection: close the mobile drawer, then navigate.
  const handleSelect = (id) => {
    setMobileOpen(false)
    navigate(id)
  }

  // Theme toggle with a "radiating void" reveal: the newly-selected theme expands
  // as a circle out from the toggle button until it covers the screen (View
  // Transitions API). The origin + final radius are passed to CSS as variables on
  // <html>. Where the API isn't available (or reduced-motion is set), it just
  // swaps instantly — same end result, no animation.
  const handleThemeToggle = (e) => {
    const root = document.documentElement
    const r = e.currentTarget.getBoundingClientRect()
    const cx = r.left + r.width / 2
    const cy = r.top + r.height / 2
    const maxR = Math.hypot(
      Math.max(cx, window.innerWidth - cx),
      Math.max(cy, window.innerHeight - cy),
    )
    root.style.setProperty('--awcp-tx', `${cx}px`)
    root.style.setProperty('--awcp-ty', `${cy}px`)
    root.style.setProperty('--awcp-tr', `${maxR}px`)
    const swap = () => {
      // Toggle the class synchronously so the View Transition captures the new
      // theme; toggleTheme() then keeps React state + localStorage in sync.
      root.classList.toggle('dark', !isDark)
      toggleTheme()
    }
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (document.startViewTransition && !reduce) {
      document.startViewTransition(swap)
    } else {
      swap()
    }
  }

  // Icon-rail collapse only applies on desktop; the mobile drawer is always full.
  const effectiveCollapsed = collapsed && isDesktop

  const meta = PAGES_META[active] || PAGES_META.dashboard

  return (
    <div className="flex h-full overflow-x-hidden bg-[#f3f5f3]">
      {booting && <Splash leaving={splashLeaving} />}

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
          {/* ── Header: page heading on the left, status + controls on the same line ── */}
          <header className="flex flex-wrap items-center gap-3 pl-1 pt-1">
            <button
              onClick={() => setMobileOpen(true)}
              title="Open menu"
              aria-label="Open menu"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-slate-100 bg-white text-slate-500 transition hover:border-brand-200 hover:text-brand-600 lg:hidden"
            >
              <Icon name="menu" className="h-5 w-5" />
            </button>

            <div className="min-w-0">
              <h1 className="text-[26px] font-extrabold leading-tight tracking-tight text-brand-900 sm:text-[32px]">
                {meta.title}
              </h1>
              <p className="mt-1 text-sm text-slate-400">{meta.subtitle}</p>
            </div>

            <div className="ml-auto flex items-center gap-1.5 sm:gap-3">
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
                onClick={handleThemeToggle}
                title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
                aria-label="Toggle theme"
                className="grid h-10 w-10 place-items-center rounded-xl border border-slate-100 bg-white text-slate-500 transition hover:border-brand-200 hover:text-brand-600"
              >
                <Icon name={isDark ? 'sun' : 'moon'} className="h-5 w-5" />
              </button>

              <div className="relative" ref={notifRef}>
                <button
                  onClick={() => setNotifOpen((o) => !o)}
                  title="Notifications"
                  aria-haspopup="menu"
                  aria-expanded={notifOpen}
                  className="relative grid h-10 w-10 place-items-center rounded-xl border border-slate-100 bg-white text-slate-500 transition hover:border-brand-200 hover:text-brand-600"
                >
                  <Icon name="bell" className="h-5 w-5" />
                  {pendingCount > 0 && (
                    <span className="absolute -right-1 -top-1 grid h-5 min-w-[20px] place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white ring-2 ring-white">
                      {pendingCount}
                    </span>
                  )}
                </button>

                {notifOpen && (
                  <div className="absolute right-0 top-12 z-50 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card-hover">
                    <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
                      <span className="text-sm font-semibold text-brand-900">Notifications</span>
                      <span className="text-xs text-slate-400">{pendingCount} pending</span>
                    </div>
                    <div className="max-h-80 overflow-y-auto">
                      {pendingCount === 0 ? (
                        <div className="px-4 py-8 text-center text-sm text-slate-400">
                          You&rsquo;re all caught up.
                        </div>
                      ) : (
                        pendingList.map((n) => (
                          <button
                            key={n.id}
                            onClick={() => {
                              setNotifOpen(false)
                              navigate('approvals')
                            }}
                            className="flex w-full items-start gap-3 border-b border-slate-50 px-4 py-3 text-left transition last:border-0 hover:bg-slate-50"
                          >
                            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-50 text-brand-600">
                              <Icon name="bell" className="h-4 w-4" />
                            </span>
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-sm font-medium text-brand-900">
                                {n.agent_name || n.agent_id || 'Agent'}
                              </div>
                              <div className="truncate text-xs text-slate-500">
                                Awaiting approval &middot;{' '}
                                <span className="font-mono">{n.action || 'write'}</span>
                              </div>
                            </div>
                            {n.risk && (
                              <span className="mt-0.5 shrink-0 rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                                {n.risk}
                              </span>
                            )}
                          </button>
                        ))
                      )}
                    </div>
                    <button
                      onClick={() => {
                        setNotifOpen(false)
                        navigate('approvals')
                      }}
                      className="block w-full border-t border-slate-100 px-4 py-2.5 text-center text-xs font-semibold text-brand-600 transition hover:bg-brand-50"
                    >
                      Review all in Approvals &rarr;
                    </button>
                  </div>
                )}
              </div>

              <div className="relative" ref={userRef}>
                <button
                  onClick={() => setUserMenuOpen((o) => !o)}
                  aria-expanded={userMenuOpen}
                  className="flex items-center gap-2.5 rounded-xl border border-slate-100 py-1.5 pl-1.5 pr-2.5 transition hover:bg-slate-50"
                >
                  <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-[#45b06a] to-[#2f7d4f] text-xs font-bold text-white">
                    {initials}
                  </span>
                  <div className="hidden max-w-[160px] leading-tight sm:block">
                    <div className="truncate text-sm font-semibold text-brand-900" title={username}>
                      {username}
                    </div>
                    <div className="text-[11px] text-slate-400">Operator</div>
                  </div>
                  <Icon name="chevronDown" className="hidden h-4 w-4 text-slate-400 sm:block" />
                </button>

                {userMenuOpen && (
                  <div className="absolute right-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card-hover">
                    <div className="border-b border-slate-100 px-4 py-3">
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">Signed in as</div>
                      <div className="truncate text-sm font-semibold text-brand-900" title={username}>
                        {username}
                      </div>
                    </div>
                    <button
                      onClick={() => {
                        setUserMenuOpen(false)
                        logout()
                      }}
                      className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm font-semibold text-rose-600 transition hover:bg-rose-50"
                    >
                      <Icon name="logout" className="h-4 w-4" />
                      Sign out
                    </button>
                  </div>
                )}
              </div>
            </div>
          </header>

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
