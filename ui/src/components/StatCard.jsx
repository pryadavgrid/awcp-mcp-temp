const ACCENTS = {
  indigo: { bar: 'border-l-brand-500', dot: 'bg-brand-500' }, // medium teal
  emerald: { bar: 'border-l-brand-700', dot: 'bg-brand-700' }, // dark teal
  violet: { bar: 'border-l-brand-300', dot: 'bg-brand-300' }, // light teal
  rose: { bar: 'border-l-rose-500', dot: 'bg-rose-500' }, // coral alert
  amber: { bar: 'border-l-amber-500', dot: 'bg-amber-500' },
  slate: { bar: 'border-l-slate-300', dot: 'bg-slate-400' },
}

export function StatCard({ label, value, sub, accent = 'slate' }) {
  const a = ACCENTS[accent] || ACCENTS.slate
  return (
    <div className={`rounded-xl border border-slate-200 border-l-4 ${a.bar} bg-white p-5 shadow-sm`}>
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        <span className={`h-2 w-2 rounded-full ${a.dot}`} />
        {label}
      </div>
      <div className="mt-3 text-4xl font-bold leading-none text-brand-900">{value}</div>
      {sub != null && <div className="mt-2 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}
