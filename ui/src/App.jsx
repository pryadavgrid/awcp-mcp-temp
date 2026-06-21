import { useState } from 'react'
import { Sidebar } from './components/Sidebar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Radar from './pages/Radar.jsx'
import Workflows from './pages/Workflows.jsx'
import TokenMonitor from './pages/TokenMonitor.jsx'
import Hooks from './pages/Hooks.jsx'
import { usePoll } from './hooks/usePoll.js'
import { getHealth } from './api.js'
import { API_BASE } from './config.js'

const TITLES = {
  dashboard: 'Dashboard',
  radar: 'Radar',
  workflow: 'Workflow',
  tokens: 'Token Monitor',
  hooks: 'Agent Hooks',
}

export default function App() {
  const [active, setActive] = useState('dashboard')
  // One shared health poll drives the header status + sidebar connection dots.
  const { data: health, error } = usePoll(getHealth, [])

  return (
    <div className="flex h-full">
      <Sidebar active={active} onSelect={setActive} health={health} />

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
          {active === 'workflow' && <Workflows />}
          {active === 'tokens' && <TokenMonitor />}
          {active === 'hooks' && <Hooks />}
        </div>
      </main>
    </div>
  )
}
