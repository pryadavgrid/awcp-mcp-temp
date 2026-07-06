import { useState } from 'react'
import { SmokeBackground } from '../components/SmokeBackground.jsx'
import { RadarGlow } from '../components/RadarGlow.jsx'
import { toggleThemeReveal } from '../lib/themeReveal.js'
import logo from '../assets/awcp-logo-anim.webp'

// Marketing landing page shown BEFORE the login gate. Framed after the provided
// mock (nav · hero · capability bento · CTA band · footer). Built with the app's
// own utility classes (Plus Jakarta Sans + brand-green palette + shadow-card),
// which auto-remap to the dashboard's dark theme via index.css — so light and dark
// both come for free and stay uniform with the rest of the UI. Every CTA advances
// to the login page (onEnter); real routing/links slot in later.

const IconFleet = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="9" cy="8" r="3" />
    <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
    <path d="M16 6.5a2.6 2.6 0 0 1 0 5" />
    <path d="M18 19a5 5 0 0 0-3-4.6" />
  </svg>
)
const IconShield = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M12 3l7 3v5c0 4.4-3 7.7-7 9-4-1.3-7-4.6-7-9V6l7-3Z" />
    <path d="m9.2 12 1.9 1.9L15 10" />
  </svg>
)
const IconChart = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M4 4v16h16" />
    <path d="M8 15v-3M12 15V8M16 15v-5" />
  </svg>
)
const IconChecklist = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M9 6h11M9 12h11M9 18h11" />
    <path d="m3 5.5 1.3 1.3L6.8 4.3M3 11.5l1.3 1.3 2.5-2.5M3 17.5l1.3 1.3 2.5-2.5" />
  </svg>
)
const IconFlask = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M9 3h6M10 3v6l-5 8.5A2 2 0 0 0 6.7 21h10.6a2 2 0 0 0 1.7-3.5L14 9V3" />
    <path d="M7.5 15h9" />
  </svg>
)
const IconGrid = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <rect x="4" y="4" width="6.5" height="6.5" rx="1.5" />
    <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" />
    <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" />
    <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" />
  </svg>
)
const IconSun = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
  </svg>
)
const IconMoon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
  </svg>
)

const FEATURES = [
  {
    id: 'fleet',
    Icon: IconFleet,
    title: 'Agent Registry',
    body: 'See registered agents, their status, autonomy level, risk tier, and recent activity in one dashboard.',
  },
  {
    id: 'policy',
    Icon: IconShield,
    title: 'Policy Guard',
    body: 'Allow or block tools for each agent, then test the guard before the agent runs a task.',
  },
  {
    id: 'audits',
    Icon: IconChart,
    title: 'Token Monitor',
    body: 'Track token use by agent, risk level, and task so expensive runs are visible early.',
  },
  {
    id: 'workflows',
    gridClass: 'md:col-start-2',
    Icon: IconChecklist,
    title: 'Temporal Workflows',
    body: 'Run agent tasks through Temporal and watch each step, result, and failure in the UI.',
  },
  {
    id: 'sandbox',
    gridClass: 'md:col-start-4',
    Icon: IconFlask,
    title: 'Sandboxed Tools',
    body: 'Route file and shell actions through the workspace sandbox instead of touching the host directly.',
  },
]

const MOCK_SECTIONS = [
  { id: 'dashboard', label: 'Overview', metric: '12 agents', note: 'Dashboard cards' },
  { id: 'radar', label: 'Radar', metric: '8 active', note: 'Agent table' },
  { id: 'tokens', label: 'Token Monitor', metric: '41k used', note: 'Budget bars' },
  { id: 'policy', label: 'Policy + Workflow', metric: '1 pending', note: 'Guarded task' },
]

function MockBadge({ children, tone = 'green' }) {
  const tones = {
    green: 'bg-brand-50 text-brand-700 ring-brand-600/20',
    amber: 'bg-amber-50 text-amber-700 ring-amber-200',
    rose: 'bg-rose-50 text-rose-700 ring-rose-200',
    slate: 'bg-slate-100 text-slate-600 ring-slate-200',
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold ring-1 ring-inset ${tones[tone]}`}>
      {children}
    </span>
  )
}

function MockStat({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">{label}</div>
      <div className="mt-1 text-xl font-extrabold text-brand-900">{value}</div>
      <div className="mt-1 truncate text-xs text-slate-500">{sub}</div>
    </div>
  )
}

function MockTokenBar({ label, value, used }) {
  return (
    <div className="rounded-lg bg-white p-3">
      <div className="mb-2 flex items-center justify-between gap-3 text-xs">
        <span className="font-bold text-brand-900">{label}</span>
        <span className="font-mono text-[11px] text-slate-500">{used}</span>
      </div>
      <div className="h-2 rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-brand-500 transition-all duration-300" style={{ width: `${value}%` }} />
      </div>
    </div>
  )
}

function WorkforcePlaneMock() {
  const [activeId, setActiveId] = useState('dashboard')
  const active = MOCK_SECTIONS.find((s) => s.id === activeId) || MOCK_SECTIONS[0]

  const renderMockBody = () => {
    if (active.id === 'dashboard') {
      return (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <MockStat label="Agents Running" value="12" sub="8 active, 3 restricted" />
          <MockStat label="Workflows" value="3" sub="Temporal tasks running" />
          <MockStat label="Tool Calls" value="18" sub="governed executions" />
          <MockStat label="Quarantine" value="1" sub="waiting for checks" />
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 sm:col-span-2 lg:col-span-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-xs font-bold text-brand-900">Governed Activity</span>
              <MockBadge>live</MockBadge>
            </div>
            <div className="flex h-24 items-end gap-2">
              {[28, 54, 42, 70, 62, 86, 58, 74, 48, 66, 80, 52].map((h, i) => (
                <div key={i} className="flex-1 rounded-t-md bg-brand-500/80" style={{ height: `${h}%` }} />
              ))}
            </div>
          </div>
        </div>
      )
    }

    if (active.id === 'radar') {
      const agents = [
        ['writer-agent', 'active', 'low', '2m ago'],
        ['search-agent', 'restricted', 'medium', '9m ago'],
        ['vision-agent', 'active', 'low', '12m ago'],
        ['legacy-bot', 'quarantine', 'high', '1h ago'],
      ]
      return (
        <div className="overflow-hidden rounded-xl border border-slate-200">
          <div className="grid grid-cols-[1.2fr_0.9fr_0.7fr_0.8fr] bg-slate-50 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">
            <span>Agent</span>
            <span>Status</span>
            <span>Risk</span>
            <span>Last Seen</span>
          </div>
          {agents.map(([name, status, risk, seen]) => (
            <div key={name} className="grid grid-cols-[1.2fr_0.9fr_0.7fr_0.8fr] items-center border-t border-slate-200 bg-white px-3 py-2.5 text-xs">
              <span className="truncate font-bold text-brand-900">{name}</span>
              <span>
                <MockBadge tone={status === 'quarantine' ? 'rose' : status === 'restricted' ? 'amber' : 'green'}>
                  {status}
                </MockBadge>
              </span>
              <span className="text-slate-500">{risk}</span>
              <span className="text-slate-500">{seen}</span>
            </div>
          ))}
        </div>
      )
    }

    if (active.id === 'tokens') {
      return (
        <div className="grid gap-3 lg:grid-cols-[1fr_0.85fr]">
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="mb-3 text-xs font-bold text-brand-900">Token use by agent</div>
            <div className="space-y-2">
              <MockTokenBar label="writer-agent" value={78} used="18k / 25k" />
              <MockTokenBar label="search-agent" value={52} used="12k / 25k" />
              <MockTokenBar label="vision-agent" value={44} used="11k / 25k" />
            </div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="mb-3 text-xs font-bold text-brand-900">Risk budget</div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-white p-3">
                <div className="text-lg font-extrabold text-brand-900">21k</div>
                <div className="text-[10px] font-bold uppercase text-slate-400">low</div>
              </div>
              <div className="rounded-lg bg-white p-3">
                <div className="text-lg font-extrabold text-amber-600">14k</div>
                <div className="text-[10px] font-bold uppercase text-slate-400">medium</div>
              </div>
              <div className="rounded-lg bg-white p-3">
                <div className="text-lg font-extrabold text-rose-600">6k</div>
                <div className="text-[10px] font-bold uppercase text-slate-400">high</div>
              </div>
            </div>
          </div>
        </div>
      )
    }

    return (
      <div className="grid gap-3 lg:grid-cols-[0.9fr_1.1fr]">
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="mb-3 text-xs font-bold text-brand-900">Policy Guard</div>
          <div className="space-y-2">
            {[
              ['shell.run', 'blocked', 'rose'],
              ['web.search', 'allowed', 'green'],
              ['file.write', 'approval', 'amber'],
            ].map(([tool, state, tone]) => (
              <div key={tool} className="flex items-center justify-between rounded-lg bg-white px-3 py-2">
                <span className="text-xs font-bold text-brand-900">{tool}</span>
                <MockBadge tone={tone}>{state}</MockBadge>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-xs font-bold text-brand-900">Temporal task flow</span>
            <MockBadge tone="amber">pending approval</MockBadge>
          </div>
          <div className="space-y-2">
            {['Register agent', 'Check policy', 'Request approval', 'Run sandboxed tool'].map((step, i) => (
              <div key={step} className="flex items-center gap-3 rounded-lg bg-white px-3 py-2">
                <span className={`grid h-6 w-6 place-items-center rounded-full text-[10px] font-extrabold ${i < 2 ? 'bg-brand-500 text-[#04240f]' : 'bg-slate-200 text-slate-500'}`}>
                  {i + 1}
                </span>
                <span className="text-xs font-medium text-slate-600">{step}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <section className="pb-12 pt-3">
      <div className="mb-5 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-600">
            Dashboard Demo
          </div>
          <h2 className="text-2xl font-extrabold tracking-tight text-brand-900 sm:text-3xl">
            Workforce Plane Preview
          </h2>
        </div>
        <p className="max-w-md text-sm text-slate-500">
          Click a section to see the preview change.
        </p>
      </div>

      <div className="mx-auto max-w-5xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
        <div className="grid md:grid-cols-[180px_1fr]">
          <div className="border-b border-slate-200 bg-slate-50 p-3 md:border-b-0 md:border-r">
            <div className="mb-3 flex items-center gap-2 px-2">
              <img src={logo} alt="AWCP" className="h-8 w-8 rounded-lg" />
              <div className="min-w-0">
                <div className="truncate text-xs font-extrabold text-brand-900">Agent Workforce</div>
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                  Control Plane
                </div>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-1.5 md:grid-cols-1">
              {MOCK_SECTIONS.map((section) => {
                const selected = section.id === activeId
                return (
                  <button
                    key={section.id}
                    type="button"
                    onClick={() => setActiveId(section.id)}
                    className={`rounded-lg px-2.5 py-2 text-left text-[11px] font-semibold transition ${
                      selected
                        ? 'bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-600/20'
                        : 'text-slate-500 hover:bg-white hover:text-brand-700'
                    }`}
                  >
                    {section.label}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="p-4 sm:p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-lg font-extrabold text-brand-900">{active.label}</div>
                <div className="text-xs text-slate-500">{active.note}</div>
              </div>
              <div className="rounded-xl bg-brand-50 px-3 py-2 text-right ring-1 ring-inset ring-brand-600/15">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                  Current
                </div>
                <div className="text-sm font-extrabold text-brand-700">{active.metric}</div>
              </div>
            </div>

            {renderMockBody()}
          </div>
        </div>
      </div>
    </section>
  )
}

export default function Landing({ onEnter }) {
  const [isDark, setIsDark] = useState(
    () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  )
  const toggleTheme = (e) => toggleThemeReveal(e, isDark, setIsDark)
  const enter = () => onEnter && onEnter()

  return (
    <div className="relative min-h-screen bg-[#f3f5f3] font-sans text-brand-900">
      {/* Dynamic smoke background (theme-aware). Sits behind all content; the page
          bg remains the fallback if WebGL is unavailable. */}
      <SmokeBackground dark={isDark} />

      {/* ── Nav ─────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-[#f3f5f3]/80 backdrop-blur dark:bg-[#0e1512]/80">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
          <div className="flex items-center gap-2.5">
            <img src={logo} alt="AWCP" className="h-12 w-12 rounded-lg" />
            <span className="text-base font-extrabold tracking-tight text-brand-900">
              Agent Workforce Control Plane
            </span>
          </div>
          <div className="flex items-center gap-5 sm:gap-7">
            <nav className="hidden items-center gap-6 text-sm sm:flex">
              <a
                href="#features"
                className="font-semibold text-brand-600 transition hover:text-brand-700"
              >
                Features
              </a>
              <a
                href="#how-it-works"
                className="font-medium text-slate-500 transition hover:text-brand-700"
              >
                Docs
              </a>
            </nav>
            <button
              onClick={toggleTheme}
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              aria-label="Toggle theme"
              className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:text-brand-600"
            >
              {isDark ? <IconSun className="h-5 w-5" /> : <IconMoon className="h-5 w-5" />}
            </button>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto max-w-6xl px-5">
        {/* ── Hero ──────────────────────────────────────────────────────── */}
        <section className="relative flex flex-col items-center py-20 text-center sm:py-28">
          {/* Radar sits between the smoke and the copy (section content is z-10). */}
          <RadarGlow />
          <div className="relative z-10 flex flex-col items-center">
            <span className="mb-7 inline-flex items-center gap-2 rounded-full bg-brand-50 px-3 py-1 font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-brand-600 ring-1 ring-inset ring-brand-600/20">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />
              v2.4.0 engine active
            </span>
            <h1 className="max-w-3xl text-4xl font-extrabold leading-[1.08] tracking-tight text-brand-900 sm:text-6xl">
              Govern, Manage, and Orchestrate Your{' '}
              <span className="italic text-brand-600">Agent Workforce.</span>
            </h1>
            <p className="mt-6 max-w-xl text-base text-slate-600 sm:text-lg">
              A comprehensive AI orchestration platform designed for enterprise governance, precision
              agent control, and token monitoring.
            </p>
            <button
              onClick={enter}
              className="mt-9 inline-flex items-center gap-2.5 rounded-full bg-brand-500 px-6 py-3.5 text-sm font-bold text-[#04240f] shadow-card transition hover:brightness-105 active:scale-[0.99]"
            >
              Get Started
              <IconGrid className="h-[18px] w-[18px]" />
            </button>
          </div>
        </section>

        {/* ── Capabilities ──────────────────────────────────────────────── */}
        <section id="features" className="pb-8">
          <div className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-600">
            What AWCP Shows
          </div>
          <h2 className="mb-8 text-2xl font-extrabold tracking-tight text-brand-900 sm:text-3xl">
            Built for Your Agent Workforce
          </h2>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-6 md:auto-rows-fr">
            {FEATURES.map(({ id, gridClass = '', Icon, title, body }) => (
              <div
                key={id}
                className={`group flex min-h-56 flex-col rounded-2xl border border-slate-200 bg-white p-6 shadow-card transition hover:-translate-y-0.5 hover:shadow-card-hover md:col-span-2 ${gridClass}`}
              >
                <div className="mb-8 flex h-11 items-start">
                  <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-600/15">
                    <Icon className="h-5 w-5" />
                  </span>
                </div>
                <div>
                  <h3 className="text-lg font-bold text-brand-900">{title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-slate-500">{body}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <WorkforcePlaneMock />

        {/* ── How it works ──────────────────────────────────────────────── */}
        <section id="how-it-works" className="scroll-mt-24 py-16">
          <div className="mb-8 text-center">
            <div className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-600">
              How it works
            </div>
            <h2 className="text-2xl font-extrabold tracking-tight text-brand-900 sm:text-3xl">
              Control without the busywork
            </h2>
          </div>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {[
              ['Agents check in', 'Your agents sign themselves in and appear on the dashboard automatically, with no manual setup as the fleet grows.'],
              ['Actions get checked', 'Before an agent does anything risky, it’s checked against your rules and blocked or paused when needed.'],
              ['You stay in control', 'Approve, pause, or adjust any agent, and set limits that keep the whole fleet within bounds.'],
              ['Everything’s on record', 'Every action is written to a tamper-proof history you can trust, review, and hand to auditors.'],
            ].map(([title, body], i) => (
              <div key={title} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
                <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 text-sm font-extrabold text-white">
                  {i + 1}
                </div>
                <h3 className="mt-4 text-base font-bold text-brand-900">{title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-500">{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── Built for trust ───────────────────────────────────────────── */}
        <section className="pb-4">
          <div className="rounded-3xl border border-slate-200 bg-white px-6 py-12 text-center shadow-card sm:px-12">
            <div className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-600">
              Built for trust
            </div>
            <h2 className="mx-auto max-w-2xl text-2xl font-extrabold tracking-tight text-brand-900 sm:text-3xl">
              Safety comes built in
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-slate-600">
              Approvals, a tamper-proof history, spending limits, safe tool execution, and role-based sign-in are all part
              of the platform, not extra add-ons. So your team can use AI agents and still know exactly what happened
              and why.
            </p>
          </div>
        </section>

        {/* ── CTA band ──────────────────────────────────────────────────── */}
        <section className="py-16">
          <div className="relative overflow-hidden rounded-3xl border border-slate-200 bg-white px-6 py-16 text-center shadow-card">
            {/* soft green glow, static */}
            <div
              className="pointer-events-none absolute inset-0"
              style={{
                background:
                  'radial-gradient(600px 300px at 20% 120%, rgba(69,176,106,0.16), transparent 60%), radial-gradient(500px 260px at 85% -10%, rgba(69,176,106,0.12), transparent 60%)',
              }}
            />
            <div className="relative">
              <h2 className="mx-auto max-w-2xl text-2xl font-extrabold tracking-tight text-brand-900 sm:text-4xl">
                Ready to take command of your AI workforce?
              </h2>
              <button
                onClick={enter}
                className="mt-8 rounded-full bg-brand-500 px-7 py-3.5 text-sm font-bold text-[#04240f] shadow-card transition hover:brightness-105 active:scale-[0.99]"
              >
                Get Started Today
              </button>
            </div>
          </div>
        </section>
      </main>

      {/* ── Footer ──────────────────────────────────────────────────────── */}
      <footer className="relative z-10 border-t border-slate-200 py-8 text-center">
        <p className="font-mono text-xs text-slate-400">Copyright © 2026 Agent Workforce Control Plane.</p>
      </footer>
    </div>
  )
}
