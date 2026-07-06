import { useEffect, useLayoutEffect, useRef, useState } from 'react'

// Lightweight, dependency-free SVG charts tuned to the forest-green theme.
// Each is interactive (hover reveals exact values) and crisp at any width — we
// measure the container and draw in real pixels instead of stretching a fixed
// viewBox, so lines and dots never distort.

function useWidth() {
  const ref = useRef(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver((entries) => setW(entries[0].contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

// Catmull-Rom → cubic-bezier, so the line is smoothly curved rather than jagged.
function smoothPath(pts) {
  if (pts.length === 0) return ''
  if (pts.length === 1) return `M ${pts[0][0]},${pts[0][1]}`
  let d = `M ${pts[0][0]},${pts[0][1]}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[i + 2] || p2
    const c1x = p1[0] + (p2[0] - p0[0]) / 6
    const c1y = p1[1] + (p2[1] - p0[1]) / 6
    const c2x = p2[0] - (p3[0] - p1[0]) / 6
    const c2y = p2[1] - (p3[1] - p1[1]) / 6
    d += ` C ${c1x},${c1y} ${c2x},${c2y} ${p2[0]},${p2[1]}`
  }
  return d
}

// ── Area / line chart ────────────────────────────────────────────────────────
// `series`: [{ label, color, values: number[] }] — one or more smooth,
// gradient-filled trend lines sharing an x-axis. A hover scrub line drops a
// marker on every series and a tooltip lists each one's value at that point.
export function AreaChart({ series, height = 140, formatValue = (v) => v }) {
  const [ref, w] = useWidth()
  const [hover, setHover] = useState(null)
  const idBase = useRef(`a${Math.random().toString(36).slice(2)}`).current

  const pad = { t: 22, b: 10, x: 6 }
  const innerW = Math.max(1, w - pad.x * 2)
  const innerH = height - pad.t - pad.b
  const n = series[0]?.values.length || 0
  const max = Math.max(1, ...series.flatMap((s) => s.values))
  const x = (i) => pad.x + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW)
  const y = (v) => pad.t + innerH - (v / max) * innerH

  const built = series.map((s, si) => {
    const pts = s.values.map((v, i) => [x(i), y(v)])
    const line = smoothPath(pts)
    const area =
      n > 0 ? `${line} L ${x(n - 1)},${pad.t + innerH} L ${x(0)},${pad.t + innerH} Z` : ''
    return { ...s, gid: `${idBase}-${si}`, line, area }
  })

  const onMove = (e) => {
    if (n === 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    const mx = e.clientX - rect.left
    let idx = Math.round(((mx - pad.x) / innerW) * (n - 1))
    setHover(Math.max(0, Math.min(n - 1, idx)))
  }

  const topY = hover != null ? Math.min(...series.map((s) => y(s.values[hover] || 0))) : 0

  return (
    <div ref={ref} className="relative w-full select-none" style={{ height }}>
      {w > 0 && (
        <svg
          width={w}
          height={height}
          className="overflow-visible"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            {built.map((s) => (
              <linearGradient key={s.gid} id={s.gid} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity="0.22" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0" />
              </linearGradient>
            ))}
          </defs>

          {/* baseline */}
          <line
            x1={pad.x}
            x2={w - pad.x}
            y1={pad.t + innerH}
            y2={pad.t + innerH}
            className="stroke-slate-100 dark:stroke-white/10"
            strokeWidth="1"
          />

          {built.map((s) => (
            <g key={s.gid}>
              {s.area && <path d={s.area} fill={`url(#${s.gid})`} />}
              {s.line && (
                <path
                  d={s.line}
                  fill="none"
                  stroke={s.color}
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}
            </g>
          ))}

          {hover != null && (
            <g>
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={pad.t - 4}
                y2={pad.t + innerH}
                stroke="#94a3b8"
                strokeOpacity="0.45"
                strokeWidth="1.5"
                strokeDasharray="3 3"
              />
              {series.map((s, si) => (
                <circle
                  key={si}
                  cx={x(hover)}
                  cy={y(s.values[hover] || 0)}
                  r="4.5"
                  fill="#fff"
                  stroke={s.color}
                  strokeWidth="2.5"
                />
              ))}
            </g>
          )}
        </svg>
      )}

      {hover != null && w > 0 && (
        <div
          className="pointer-events-none absolute z-20 flex -translate-x-1/2 -translate-y-full items-center gap-2.5 whitespace-nowrap rounded-lg bg-brand-900 px-2 py-1 text-[11px] font-semibold text-white shadow-card"
          style={{ left: x(hover), top: topY - 8 }}
        >
          {series.map((s, si) => (
            <span key={si} className="flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: s.color }}
              />
              {formatValue(s.values[hover] || 0)}
              <span className="font-normal text-white/55">{s.label}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Horizontal bar list ──────────────────────────────────────────────────────
// `items`: [{ label, value, meta, tone }]. Bars are sized relative to the
// largest value (a clean "top N" comparison), and animate in on first paint.
const BAR_TONE = {
  ok: 'bg-gradient-to-r from-brand-400 to-brand-600',
  warn: 'bg-gradient-to-r from-amber-400 to-amber-500',
  exhausted: 'bg-gradient-to-r from-rose-400 to-rose-500',
}

export function BarList({ items, formatValue = (v) => v }) {
  const [grown, setGrown] = useState(false)
  const [hover, setHover] = useState(null)
  useEffect(() => {
    const id = requestAnimationFrame(() => setGrown(true))
    return () => cancelAnimationFrame(id)
  }, [])
  const max = Math.max(1, ...items.map((i) => i.value))

  return (
    <div className="space-y-3">
      {items.map((it, i) => {
        const pct = Math.max(3, Math.round((it.value / max) * 100))
        const active = hover === i
        return (
          <div
            key={it.label + i}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          >
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="truncate font-medium text-brand-900">{it.label}</span>
              <span
                className={`shrink-0 tabular-nums transition-colors ${active ? 'font-semibold text-brand-700' : 'text-slate-400'}`}
              >
                {formatValue(it.value)}
                {it.meta && <span className="text-slate-400"> · {it.meta}</span>}
              </span>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-white/10">
              <div
                className={`h-full rounded-full ${BAR_TONE[it.tone] || BAR_TONE.ok} transition-all duration-700 ease-out ${active ? 'brightness-105' : ''}`}
                style={{ width: grown ? `${pct}%` : '0%' }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Semicircular gauge ───────────────────────────────────────────────────────
// A speedometer-style gauge: a 180° track, a colored value arc, and a needle.
// Used to show each agent's token-budget usage at a glance.
const GAUGE_TONE = {
  ok: ['#7fbd93', '#3a7d52'], // brand-300 → brand-500
  warn: ['#fcd34d', '#f59e0b'],
  exhausted: ['#fb7185', '#f43f5e'],
}

// Angle measured clockwise from 12 o'clock; -90° is the left end, +90° the right.
function gaugePoint(cx, cy, r, deg) {
  const a = ((deg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) }
}
function gaugeArc(cx, cy, r, startDeg, endDeg) {
  const s = gaugePoint(cx, cy, r, startDeg)
  const e = gaugePoint(cx, cy, r, endDeg)
  const large = endDeg - startDeg <= 180 ? 0 : 1
  return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 1 ${e.x} ${e.y}`
}

export function Gauge({ value, max = 100, label, sub, tone = 'ok', display }) {
  const ratio = Math.max(0, Math.min(1, max ? value / max : 0))
  const [from, to] = GAUGE_TONE[tone] || GAUGE_TONE.ok
  const gid = useRef(`gauge${Math.random().toString(36).slice(2)}`).current

  const W = 150
  const H = 90
  const cx = W / 2
  const cy = 80
  const r = 58
  const sw = 13
  const valueDeg = -90 + ratio * 180
  const tip = gaugePoint(cx, cy, r - 4, valueDeg)
  const tail = gaugePoint(cx, cy, 10, valueDeg + 180)
  const shown = display != null ? display : `${Math.round(value)}%`

  return (
    <div className="group/gauge flex flex-col items-center rounded-xl px-1 py-2 transition hover:bg-slate-50 dark:hover:bg-white/5">
      {label && (
        <div className="max-w-full truncate text-[10px] font-semibold uppercase tracking-wide text-slate-400">
          {label}
        </div>
      )}
      <div className="text-lg font-extrabold leading-tight text-brand-900">{shown}</div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={from} />
            <stop offset="100%" stopColor={to} />
          </linearGradient>
        </defs>

        {/* track */}
        <path
          d={gaugeArc(cx, cy, r, -90, 90)}
          fill="none"
          strokeWidth={sw}
          strokeLinecap="round"
          className="stroke-slate-100 dark:stroke-white/10"
        />
        {/* value arc */}
        {ratio > 0 && (
          <path
            d={gaugeArc(cx, cy, r, -90, valueDeg)}
            fill="none"
            stroke={`url(#${gid})`}
            strokeWidth={sw}
            strokeLinecap="round"
            className="transition-all duration-700 ease-out"
          />
        )}
        {/* needle */}
        <line
          x1={tail.x}
          y1={tail.y}
          x2={tip.x}
          y2={tip.y}
          stroke="#15311f"
          strokeWidth="2.5"
          strokeLinecap="round"
          className="transition-all duration-700 ease-out"
        />
        <circle cx={cx} cy={cy} r="5" fill="#15311f" />
        <circle cx={cx} cy={cy} r="2" fill="#fff" />

        {/* min / max ticks */}
        <text x={cx - r} y={cy + 12} textAnchor="middle" className="fill-slate-400 text-[8px]">
          0
        </text>
        <text x={cx + r} y={cy + 12} textAnchor="middle" className="fill-slate-400 text-[8px]">
          {max}
        </text>
      </svg>

      {sub && (
        <div className="-mt-1 max-w-full truncate text-[11px] text-slate-400">{sub}</div>
      )}
    </div>
  )
}

// ── Interactive donut ────────────────────────────────────────────────────────
// `segments`: [{ label, value, color }]. Hovering a slice (or its legend row)
// pops the slice and shows that slice's value in the center.
export function DonutChart({ segments, size = 132, stroke = 16, unit = '' }) {
  const [hover, setHover] = useState(null)
  const r = (size - stroke) / 2
  const C = 2 * Math.PI * r
  const total = segments.reduce((s, x) => s + x.value, 0)
  const active = hover != null ? segments[hover] : null
  const centerTop = active ? active.value : total
  const centerBottom = active ? active.label : unit || 'total'

  let acc = 0
  return (
    <div className="flex items-center gap-5">
      <div className="relative shrink-0" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            strokeWidth={stroke}
            className="stroke-slate-100 dark:stroke-white/10"
          />
          {total > 0 &&
            segments.map((seg, i) => {
              if (seg.value <= 0) return null
              const len = (seg.value / total) * C
              const el = (
                <circle
                  key={i}
                  cx={size / 2}
                  cy={size / 2}
                  r={r}
                  fill="none"
                  stroke={seg.color}
                  strokeWidth={hover === i ? stroke + 3 : stroke}
                  strokeDasharray={`${Math.max(0, len - 2)} ${C - Math.max(0, len - 2)}`}
                  strokeDashoffset={-acc}
                  strokeLinecap="round"
                  className="cursor-pointer transition-all duration-200"
                  style={{ opacity: hover == null || hover === i ? 1 : 0.4 }}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                />
              )
              acc += len
              return el
            })}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-extrabold leading-none text-brand-900">{centerTop}</span>
          <span className="text-[11px] capitalize text-slate-400">{centerBottom}</span>
        </div>
      </div>

      <div className="space-y-2 text-xs">
        {segments.map((seg, i) => (
          <div
            key={i}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            className={`flex cursor-pointer items-center gap-2 rounded-lg px-1.5 py-0.5 transition-colors ${hover === i ? 'bg-slate-50 dark:bg-white/5' : ''}`}
          >
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: seg.color }} />
            <span className="text-slate-500">{seg.label}</span>
            <span className="ml-auto pl-3 font-semibold text-brand-900">{seg.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
