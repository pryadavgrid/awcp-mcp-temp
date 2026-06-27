import { useMemo, useRef, useState } from 'react'
import { usePoll } from '../hooks/usePoll.js'
import { getNeo4jGraph } from '../api.js'

// A dependency-free, INTERACTIVE node-link view of the Neo4j projection. Nodes
// start in columns by type (Agent → Workflow → Step → Tool) but can be dragged
// anywhere; click a node to highlight it + its connections. Edges are curved.

const COLORS = {
  agent: '#6366f1', // indigo
  workflow: '#64748b', // slate
  step: '#10b981', // emerald (allow)
  stepBlocked: '#f43f5e', // rose (blocked/deny)
  tool: '#f59e0b', // amber
  policy: '#a855f7', // purple — gate rule that blocked a step
  error: '#dc2626', // red — a failed step
  skill: '#14b8a6', // teal — an A2A AgentCard capability the agent advertises
}
// workflow kept for safety but not rendered; columns re-spaced without it
const COLX = { skill: 0.05, agent: 0.2, workflow: 0.2, step: 0.42, tool: 0.6, policy: 0.78, error: 0.92 }
const RADIUS = { agent: 10, workflow: 10, step: 8, tool: 7, policy: 8, error: 7, skill: 7 }
const PAD_Y = 30

const truncate = (s, n = 18) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '')
const nodeColor = (n) =>
  n.type === 'step' && (n.outcome === 'blocked' || n.decision === 'deny')
    ? COLORS.stepBlocked
    : COLORS[n.type] || COLORS.step

// Initial column layout — used as the starting position before any dragging.
function layout(nodes, width, height) {
  const cols = { skill: [], agent: [], workflow: [], step: [], tool: [], policy: [], error: [] }
  for (const n of nodes) (cols[n.type] || cols.step).push(n)
  cols.step.sort((a, b) => (a.ts || 0) - (b.ts || 0))
  for (const t of ['skill', 'agent', 'workflow', 'tool', 'policy', 'error'])
    cols[t].sort((a, b) => String(a.label).localeCompare(String(b.label)))
  const pos = {}
  for (const type of Object.keys(cols)) {
    const arr = cols[type]
    arr.forEach((node, i) => {
      pos[node.id] = {
        x: width * COLX[type],
        y: PAD_Y + ((i + 1) / (arr.length + 1)) * (height - 2 * PAD_Y),
      }
    })
  }
  return pos
}

// Gentle quadratic curve so parallel/overlapping edges fan out a little.
function edgePath(a, b) {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const dist = Math.hypot(dx, dy) || 1
  const curve = Math.min(46, dist * 0.18)
  const cx = (a.x + b.x) / 2 + (-dy / dist) * curve
  const cy = (a.y + b.y) / 2 + (dx / dist) * curve
  return `M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`
}

export default function Neo4jGraph({ workflow }) {
  const { data, loading, error } = usePoll(() => getNeo4jGraph(workflow), [workflow])
  // Workflow/task nodes stay in Neo4j (for queries + the workflow filter) but are
  // hidden from this view: their INCLUDES fan-out was the main clutter, and the
  // Timeline view already groups steps by run. The graph focuses on the relations
  // you can't see elsewhere: Agent → Step-chain → Tool / Policy / Error.
  const nodes = (data?.nodes || []).filter((n) => n.type !== 'workflow')
  const edges = (data?.edges || []).filter((e) => e.type !== 'INCLUDES')

  const svgRef = useRef(null)
  const dragRef = useRef(null) // { id, dx, dy, moved, downX, downY }
  const [override, setOverride] = useState({}) // id -> {x,y} from manual dragging
  const [selected, setSelected] = useState(null)

  const W = 960
  const maxCol = useMemo(() => {
    const c = {}
    for (const n of nodes) c[n.type] = (c[n.type] || 0) + 1
    return Math.max(...Object.values(c), 1)
  }, [nodes])
  const H = Math.max(420, maxCol * 56)
  const base = useMemo(() => layout(nodes, W, H), [nodes, H])
  const posOf = (id) => override[id] || base[id] || { x: 0, y: 0 }

  // neighbours of the selected node (for highlight / fade)
  const neighbours = useMemo(() => {
    if (!selected) return null
    const set = new Set([selected])
    for (const e of edges) {
      if (e.source === selected) set.add(e.target)
      if (e.target === selected) set.add(e.source)
    }
    return set
  }, [selected, edges])

  function clientToSvg(clientX, clientY) {
    const svg = svgRef.current
    const m = svg?.getScreenCTM()
    if (!svg || !m) return { x: 0, y: 0 }
    const pt = svg.createSVGPoint()
    pt.x = clientX
    pt.y = clientY
    const p = pt.matrixTransform(m.inverse())
    return { x: p.x, y: p.y }
  }

  function onNodeDown(e, id) {
    e.stopPropagation()
    const { x, y } = clientToSvg(e.clientX, e.clientY)
    const cur = posOf(id)
    dragRef.current = { id, dx: cur.x - x, dy: cur.y - y, moved: false, downX: e.clientX, downY: e.clientY }
    svgRef.current?.setPointerCapture?.(e.pointerId)
  }
  function onMove(e) {
    const d = dragRef.current
    if (!d) return
    if (Math.hypot(e.clientX - d.downX, e.clientY - d.downY) > 3) d.moved = true
    const { x, y } = clientToSvg(e.clientX, e.clientY)
    setOverride((prev) => ({ ...prev, [d.id]: { x: x + d.dx, y: y + d.dy } }))
  }
  function onUp() {
    const d = dragRef.current
    if (d && !d.moved) setSelected((s) => (s === d.id ? null : d.id)) // tap = toggle select
    dragRef.current = null
  }

  if (data && data.enabled === false)
    return (
      <Empty>
        Neo4j projection is off. Start it with{' '}
        <span className="font-mono">docker compose -f observability/docker-compose.yml up -d neo4j</span>, then{' '}
        <span className="font-mono">POST /context-graph/neo4j/backfill</span>.
      </Empty>
    )
  if (loading && !data) return <Empty>Loading graph…</Empty>
  if (error) return <Empty tone="rose">{String(error)}</Empty>
  if (!nodes.length) return <Empty>No graph yet — run an agent that calls a tool.</Empty>

  return (
    <div className="space-y-3">
      <Legend counts={data?.stats?.counts || {}} edges={edges.length} selected={!!selected} onClear={() => setSelected(null)} />
      <div className="overflow-x-auto">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          style={{ minWidth: 660, touchAction: 'none', userSelect: 'none' }}
          className="rounded-lg bg-slate-50"
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerDown={() => setSelected(null)}
        >
          <defs>
            <marker id="cg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" />
            </marker>
            <marker id="cg-arrow-hi" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#6366f1" />
            </marker>
          </defs>

          {/* edges (curved) */}
          {edges.map((e, i) => {
            const a = posOf(e.source)
            const b = posOf(e.target)
            if (!a || !b) return null
            const isNext = e.type === 'NEXT'
            const connected = neighbours && (e.source === selected || e.target === selected)
            const dim = neighbours && !connected
            const baseStroke =
              e.type === 'NEXT' ? '#94a3b8'
              : e.type === 'BLOCKED_BY' ? '#f87171'
              : e.type === 'RAISED' ? '#fb923c'
              : e.type === 'HAS_SKILL' ? '#5eead4'
              : '#dbe2ea'
            return (
              <path
                key={i}
                d={edgePath(a, b)}
                fill="none"
                stroke={connected ? '#6366f1' : baseStroke}
                strokeWidth={connected ? 2 : isNext ? 1.6 : 1}
                opacity={dim ? 0.12 : 1}
                markerEnd={isNext ? (connected ? 'url(#cg-arrow-hi)' : 'url(#cg-arrow)') : undefined}
              />
            )
          })}

          {/* nodes (draggable + selectable) */}
          {nodes.map((n) => {
            const p = posOf(n.id)
            if (!p) return null
            const r = RADIUS[n.type] || 7
            const leftLabel = n.type === 'tool' || n.type === 'error'
            const isSel = selected === n.id
            const dim = neighbours && !neighbours.has(n.id)
            return (
              <g
                key={n.id}
                opacity={dim ? 0.25 : 1}
                style={{ cursor: 'grab' }}
                onPointerDown={(e) => onNodeDown(e, n.id)}
              >
                {isSel && <circle cx={p.x} cy={p.y} r={r + 6} fill="none" stroke="#6366f1" strokeWidth="2.5" />}
                <circle cx={p.x} cy={p.y} r={isSel ? r + 1.5 : r} fill={nodeColor(n)} stroke="#fff" strokeWidth="1.5">
                  <title>{`${n.type}: ${n.label}${n.decision ? ` · ${n.decision}` : ''}`}</title>
                </circle>
                <text
                  x={leftLabel ? p.x - r - 5 : p.x + r + 5}
                  y={p.y + 3}
                  textAnchor={leftLabel ? 'end' : 'start'}
                  fontSize="10.5"
                  fontWeight={isSel ? 700 : 400}
                  fill={isSel ? '#1e293b' : '#475569'}
                  className="font-mono"
                  style={{ pointerEvents: 'none' }}
                >
                  {truncate(n.label)}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}

function Legend({ counts, edges, selected, onClear }) {
  const items = [
    ['agent', 'Agent', COLORS.agent],
    ['skill', 'Skill (A2A)', COLORS.skill],
    ['step', 'Step', COLORS.step],
    ['tool', 'Tool', COLORS.tool],
    ['policy', 'Policy', COLORS.policy],
    ['error', 'Error', COLORS.error],
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[11px] text-slate-500">
      {items.map(([k, label, color]) => (
        <span key={k} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
          {label}
          <span className="text-slate-400">{counts[k] || 0}</span>
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-px w-4 bg-slate-400" /> NEXT
        <span className="text-slate-400">· {edges} edges</span>
      </span>
      <span className="text-slate-300">· drag to move · click to highlight</span>
      {selected && (
        <button onClick={onClear} className="rounded px-2 py-0.5 text-brand-600 hover:bg-brand-50">
          clear selection
        </button>
      )}
    </div>
  )
}

function Empty({ children, tone = 'slate' }) {
  return (
    <div className={`px-5 py-12 text-center text-sm ${tone === 'rose' ? 'text-rose-600' : 'text-slate-400'}`}>
      {children}
    </div>
  )
}
