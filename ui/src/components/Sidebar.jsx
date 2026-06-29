const ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: '▦' },
  { id: 'radar', label: 'Radar', icon: '◎' },
  { id: 'approvals', label: 'Approvals', icon: '✋' },
  { id: 'workflow', label: 'Workflow', icon: '⤳' },
  { id: 'context', label: 'Context Graph', icon: '◈' },
  { id: 'tokens', label: 'Token Monitor', icon: '◔' },
  { id: 'hooks', label: 'Agent Hooks', icon: '⚓' },
  { id: 'policy', label: 'Operator Policy', icon: '⚖' },
  { id: 'sandbox', label: 'Sandbox', icon: '▣' },
]

export function Sidebar({ active, onSelect, health, approvalsCount = 0 }) {
  const temporal = health?.temporal_connected
  const otel = health?.otel_enabled
  const laminar = health?.laminar?.enabled
  const opa = health?.opa?.connected
  const sandboxStatus = health?.sandbox?.status

  return (
    <aside className="flex w-60 shrink-0 flex-col bg-brand-800 text-brand-100">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand-500 text-lg text-white shadow-sm">
          ◆
        </span>
        <div>
          <div className="text-sm font-bold leading-tight text-white">AWCP</div>
          <div className="text-[11px] leading-tight text-brand-200">Control Plane</div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {ITEMS.map((it) => {
          const isActive = active === it.id
          return (
            <button
              key={it.id}
              onClick={() => onSelect(it.id)}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${
                isActive
                  ? 'bg-white/10 font-semibold text-white ring-1 ring-inset ring-white/15'
                  : 'text-brand-200 hover:bg-white/5 hover:text-white'
              }`}
            >
              <span className="w-4 text-center text-base leading-none">{it.icon}</span>
              <span className="flex-1 text-left">{it.label}</span>
              {it.id === 'approvals' && approvalsCount > 0 && (
                <span
                  title={`${approvalsCount} approval${approvalsCount === 1 ? '' : 's'} pending`}
                  className="text-sm font-bold tabular-nums text-white"
                >
                  {approvalsCount}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      <div className="space-y-2 border-t border-white/10 px-5 py-4 text-[11px] text-brand-200">
        <ConnRow label="Temporal" ok={temporal} />
        <ConnRow label="OTel" ok={otel} />
        <ConnRow label="Laminar" ok={laminar} />
        <ConnRow label="OPA" ok={opa} />
        <ConnRow
          label="Sandbox"
          ok={sandboxStatus === 'running' || sandboxStatus === 'not_started'}
          text={sandboxStatus ? sandboxStatus.replace('_', ' ') : 'offline'}
          title={health?.sandbox?.reason || health?.sandbox?.workspace_dir}
        />
      </div>
    </aside>
  )
}

function ConnRow({ label, ok, text, title }) {
  return (
    <div className="flex items-center justify-between" title={title}>
      <span>{label}</span>
      <span className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-brand-300' : 'bg-white/25'}`} />
        <span className={ok ? 'text-brand-100' : 'text-brand-200/70'}>
          {text ?? (ok ? 'connected' : 'offline')}
        </span>
      </span>
    </div>
  )
}
