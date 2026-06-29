import logoUrl from '../assets/awcp-logo.png'
import { Icon } from './Icons.jsx'

// Two groups, matching the reference dashboard's MENU / GENERAL split. The icon
// key maps to an <Icon name=…> glyph; the id maps to a page in App.jsx.
const GROUPS = [
  {
    label: 'Control Plane',
    items: [
      { id: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
      { id: 'radar', label: 'Radar', icon: 'radar' },
      { id: 'approvals', label: 'Approvals', icon: 'approvals' },
      { id: 'workflow', label: 'Workflow', icon: 'workflow' },
    ],
  },
  {
    label: 'Governance',
    items: [
      { id: 'context', label: 'Context Graph', icon: 'context' },
      { id: 'tokens', label: 'Token Monitor', icon: 'tokens' },
      { id: 'hooks', label: 'Agent Hooks', icon: 'hooks' },
      { id: 'policy', label: 'Operator Policy', icon: 'policy' },
      { id: 'sandbox', label: 'Sandbox', icon: 'sandbox' },
    ],
  },
]

export function Sidebar({
  active,
  onSelect,
  health,
  approvalsCount = 0,
  collapsed = false,
  onToggleCollapse,
  mobileOpen = false,
  onCloseMobile,
}) {
  const temporal = health?.temporal_connected
  const otel = health?.otel_enabled
  const laminar = health?.laminar?.enabled
  const opa = health?.opa?.connected
  const sandboxStatus = health?.sandbox?.status

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-50 flex shrink-0 flex-col border-r border-slate-200/70 bg-white shadow-xl transition-[transform,width] duration-200 lg:static lg:z-auto lg:shadow-none ${
        collapsed ? 'w-64 lg:w-[76px]' : 'w-64'
      } ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
    >
      {/* Brand + collapse control. Expanded: logo + name + a collapse button.
          Collapsed: the logo itself is the button to expand again. The logo image
          is unchanged in both states. */}
      {collapsed ? (
        <div className="flex justify-center py-6">
          <button
            onClick={onToggleCollapse}
            title="Expand sidebar"
            aria-label="Expand sidebar"
            className="group grid h-10 w-10 place-items-center rounded-xl ring-1 ring-transparent transition hover:ring-brand-200"
          >
            <img src={logoUrl} alt="AWCP" className="h-9 w-9 rounded-xl transition group-hover:opacity-80" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2.5 px-6 py-6">
          <img src={logoUrl} alt="AWCP" className="h-9 w-9 shrink-0 rounded-xl" />
          <div className="flex-1 text-[19px] font-extrabold leading-none tracking-tight text-brand-900">
            AWCP
          </div>
          {/* Desktop: collapse to the icon rail. */}
          <button
            onClick={onToggleCollapse}
            title="Collapse sidebar"
            aria-label="Collapse sidebar"
            className="hidden h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-brand-600 lg:grid"
          >
            <Icon name="panelLeft" className="h-5 w-5" />
          </button>
          {/* Mobile/tablet: close the drawer. */}
          <button
            onClick={onCloseMobile}
            title="Close menu"
            aria-label="Close menu"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-brand-600 lg:hidden"
          >
            <Icon name="close" className="h-5 w-5" />
          </button>
        </div>
      )}

      <nav className={`flex-1 space-y-6 overflow-y-auto overflow-x-hidden pb-4 ${collapsed ? 'px-2.5' : 'px-4'}`}>
        {GROUPS.map((group) => (
          <div key={group.label}>
            {collapsed ? (
              <div className="mx-2 mb-2 h-px bg-slate-100" />
            ) : (
              <div className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                {group.label}
              </div>
            )}
            <div className="space-y-1">
              {group.items.map((it) => {
                const isActive = active === it.id
                return (
                  <button
                    key={it.id}
                    onClick={() => onSelect(it.id)}
                    title={collapsed ? it.label : undefined}
                    className={`group relative flex w-full items-center rounded-xl text-sm transition ${
                      collapsed ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2.5'
                    } ${
                      isActive
                        ? 'bg-brand-50 font-semibold text-brand-700'
                        : 'font-medium text-slate-500 hover:bg-slate-50 hover:text-brand-700'
                    }`}
                  >
                    {isActive && !collapsed && (
                      <span className="absolute left-0 top-1/2 h-6 -translate-y-1/2 rounded-r-full border-l-[3px] border-brand-600" />
                    )}
                    <span className="relative">
                      <Icon
                        name={it.icon}
                        className={`h-5 w-5 shrink-0 ${isActive ? 'text-brand-600' : 'text-slate-400 group-hover:text-brand-600'}`}
                      />
                      {/* collapsed: show the approvals count as a corner dot/badge on the icon */}
                      {collapsed && it.id === 'approvals' && approvalsCount > 0 && (
                        <span className="absolute -right-2 -top-2 grid h-4 min-w-[16px] place-items-center rounded-full bg-brand-600 px-1 text-[9px] font-bold text-white">
                          {approvalsCount}
                        </span>
                      )}
                    </span>
                    {!collapsed && <span className="flex-1 text-left">{it.label}</span>}
                    {!collapsed && it.id === 'approvals' && approvalsCount > 0 && (
                      <span className="grid h-5 min-w-[20px] place-items-center rounded-full bg-brand-600 px-1.5 text-[11px] font-bold text-white">
                        {approvalsCount}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* System status — the live service connection dots (hidden when collapsed). */}
      {collapsed ? (
        <div
          className="m-3 flex flex-col items-center gap-2 rounded-2xl border border-slate-100 bg-slate-50/70 py-3"
          title="System status"
        >
          <StatusDot ok={temporal} />
          <StatusDot ok={otel} />
          <StatusDot ok={laminar} />
          <StatusDot ok={opa} />
          <StatusDot ok={sandboxStatus === 'running' || sandboxStatus === 'not_started'} />
        </div>
      ) : (
        <div className="m-4 mt-0 space-y-2 rounded-2xl border border-slate-100 bg-slate-50/70 px-4 py-3.5">
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            System
          </div>
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
      )}
    </aside>
  )
}

function StatusDot({ ok }) {
  return <span className={`h-2 w-2 rounded-full ${ok ? 'bg-brand-500' : 'bg-slate-300'}`} />
}

function ConnRow({ label, ok, text, title }) {
  return (
    <div className="flex items-center justify-between text-xs" title={title}>
      <span className="text-slate-500">{label}</span>
      <span className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-brand-500' : 'bg-slate-300'}`} />
        <span className={ok ? 'font-medium text-brand-600' : 'text-slate-400'}>
          {text ?? (ok ? 'connected' : 'offline')}
        </span>
      </span>
    </div>
  )
}
