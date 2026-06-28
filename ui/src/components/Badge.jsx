const TONES = {
  green: 'bg-brand-100 text-brand-700 ring-brand-600/25',
  red: 'bg-rose-100 text-rose-700 ring-rose-600/20',
  amber: 'bg-amber-100 text-amber-800 ring-amber-700/20',
  blue: 'bg-brand-600 text-white ring-brand-700/30',
  violet: 'bg-slate-200 text-slate-700 ring-slate-500/30',
  slate: 'bg-slate-100 text-slate-600 ring-slate-500/20',
}

// Map any status / state / decision string to a tone — purely value-driven, so
// new backend states still render (they just fall back to the neutral tone).
export function toneFor(value) {
  const v = String(value || '').toLowerCase()
  if (/(^|[^a-z])(active|done|ok|complete|completed|allow|allowed|admit|admitted|resumed?)([^a-z]|$)/.test(v))
    return 'green'
  if (/(quarantin|exhaust|suspend|deny|denied|block|error|fail|stop|hard.?stop|uncontrolled|bypass|terminat|cancel|timed.?out|aborted)/.test(v))
    return 'red'
  if (/(warn|pending|recommendation|throttl|degrad|trace.?boost|safe.?profile)/.test(v))
    return 'amber'
  if (/(running|in.?progress|active.task)/.test(v)) return 'blue'
  return 'slate'
}

export function Badge({ children, tone = 'slate', title, className = '' }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
        TONES[tone] || TONES.slate
      } ${className}`}
    >
      {children}
    </span>
  )
}

// Convenience: a badge whose tone is inferred from its text.
export function StatusBadge({ value, title }) {
  if (value == null || value === '') return <span className="text-slate-400">—</span>
  return (
    <Badge tone={toneFor(value)} title={title}>
      {String(value)}
    </Badge>
  )
}
