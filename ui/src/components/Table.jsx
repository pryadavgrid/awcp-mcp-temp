// Lightweight table primitives so every view shares one consistent look.

export function Panel({ title, subtitle, right, children, className = '' }) {
  return (
    <section
      className={`overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card ${className}`}
    >
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            {title && <h2 className="text-base font-bold tracking-tight text-brand-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  )
}

export function Table({ columns, children }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-100 bg-slate-50/70 text-[11px] uppercase tracking-wider text-slate-400">
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-5 py-2.5 font-semibold">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  )
}

export function Td({ children, className = '' }) {
  return <td className={`px-5 py-3 align-middle ${className}`}>{children}</td>
}

export function EmptyRow({ colSpan, children = 'No data yet.' }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-5 py-10 text-center text-sm text-slate-400">
        {children}
      </td>
    </tr>
  )
}
