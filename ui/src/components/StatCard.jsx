import { Icon } from './Icons.jsx'

// Accent → the small status dot before the label (kept so existing pages still
// convey state, e.g. rose for "quarantined"). No left bar — the reference design
// is borderless white cards with a top-right arrow on the clickable ones.
const ACCENTS = {
  indigo: 'bg-brand-500',
  emerald: 'bg-brand-700',
  violet: 'bg-brand-300',
  rose: 'bg-rose-500',
  amber: 'bg-amber-500',
  slate: 'bg-slate-300',
}

// A single stat tile. `featured` paints it as a deep-green filled card (the hero
// tile); `onClick` makes the whole card a button with a top-right arrow that
// drills into the matching page. `delta` renders a small chip beside `sub`.
export function StatCard({
  label,
  value,
  sub,
  accent = 'slate',
  featured = false,
  delta,
  onClick,
}) {
  const dot = ACCENTS[accent] || ACCENTS.slate
  const clickable = typeof onClick === 'function'

  const base =
    'group block w-full rounded-2xl p-5 text-left shadow-card transition'
  const skin = featured
    ? 'bg-gradient-to-br from-[#45b06a] via-[#348a52] to-[#256b3c] text-white'
    : 'border border-slate-100 bg-white'
  const hover = clickable ? ' hover:-translate-y-0.5 hover:shadow-card-hover' : ''

  const labelCls = featured
    ? 'text-[13px] font-semibold text-white/85'
    : 'text-[13px] font-semibold text-slate-500'
  const valueCls = featured
    ? 'mt-4 text-4xl font-extrabold leading-none tracking-tight text-white'
    : 'mt-4 text-4xl font-extrabold leading-none tracking-tight text-brand-900'
  const subCls = featured ? 'text-xs text-white/80' : 'text-xs text-slate-400'

  const Comp = clickable ? 'button' : 'div'

  return (
    <Comp type={clickable ? 'button' : undefined} onClick={onClick} className={`${base} ${skin}${hover}`}>
      <div className="flex items-start justify-between gap-3">
        <div className={`flex items-center gap-2 ${labelCls}`}>
          {!featured && <span className={`h-2 w-2 rounded-full ${dot}`} />}
          {label}
        </div>
        {clickable && (
          <span
            className={`grid h-8 w-8 shrink-0 place-items-center rounded-full transition ${
              featured
                ? 'bg-white text-brand-700'
                : 'border border-slate-200 text-slate-400 group-hover:border-brand-300 group-hover:text-brand-600'
            }`}
          >
            <Icon name="arrowUpRight" className="h-4 w-4" strokeWidth={2} />
          </span>
        )}
      </div>

      <div className={valueCls}>{value}</div>

      {(sub != null || delta != null) && (
        <div className="mt-2.5 flex items-center gap-2">
          {delta != null && (
            <span
              className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] font-bold ${
                featured ? 'bg-white/20 text-white' : 'bg-brand-50 text-brand-700'
              }`}
            >
              {delta}
            </span>
          )}
          {sub != null && <span className={subCls}>{sub}</span>}
        </div>
      )}
    </Comp>
  )
}
