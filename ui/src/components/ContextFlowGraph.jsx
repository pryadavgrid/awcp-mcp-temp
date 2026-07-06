import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Badge, StatusBadge } from './Badge.jsx'
import { timeAgo, fmtInt } from '../lib/format.js'

// ── Context Flow Graph ────────────────────────────────────────────────────────
// A stepwise, left-to-right React Flow view of one run's context graph, built
// ENTIRELY from live data (the evidence-ledger feed + the agent registry — no
// Neo4j required, nothing hardcoded):
//
//   [Agent card (A2A)] ─starts→ [#1 step] ─next→ [#2 step] ─next→ … (wraps rows)
//                                   │ uses               │ blocked by
//                                 [Tool]               [Policy]
//
// • The numbered spine is the run, in order — animated arrows show direction.
// • Each step hangs its Tool / Model / Policy / Error below it (directed edges).
// • The Agent node shows its A2A AgentCard (description + advertised skills).
// • Click a step → "backtrack" along the evidence ledger: earlier steps stay
//   lit (the trail that led here), later ones fade, and a detail panel shows
//   the tamper chain (prev → row hash), resume pointer and token spend.
// • Optional working-set overlay marks what a recovering agent would carry
//   forward (✓), what is stale (grey, with reasons) and the resume anchor (⚓).

// Layout constants (only geometry — every value rendered comes from the API).
const PER_ROW = 5 // steps per row before wrapping to the next line
const STEP_W = 200
const COL_X = 250 // horizontal distance between steps
const ROW_H = 265 // vertical distance between step rows (leaves an attachment band)
const ATTACH_DY = 118 // attachment lane below its step row
const ATTACH_DX = 135 // spread when one step has several attachments
const AGENT_X = 265 // horizontal distance between agent cards
const STEPS_Y = 205 // where the step rows start (below the agent lane)

const COLORS = {
  spine: '#2f6b45', // brand-600 — the next-step arrows
  spineDim: '#cbd5e1',
  start: '#94a3b8', // agent → first step
  tool: '#f59e0b', // amber
  model: '#4f9d6a', // brand-400
  policy: '#f43f5e', // rose
  error: '#fb923c', // orange
}

// Follow the app's light/dark theme (the `dark` class useTheme sets on <html>)
// so React Flow's own chrome (controls, minimap, attribution) matches. Observed
// directly because useTheme's state is per-instance and the toggle lives in App.
function useIsDark() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    const obs = new MutationObserver(() =>
      setDark(document.documentElement.classList.contains('dark')),
    )
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return dark
}

const stepKind = (step) => String(step || '').split(':')[0].toLowerCase()
const stepLabel = (step) => {
  const s = String(step || 'step')
  const i = s.indexOf(':')
  return i >= 0 ? s.slice(i + 1) : s
}
const STEP_ICONS = { tool: '⚒', llm: '✦', generate: '✦', synthesize: '✦', route: '⇢', delegate: '⇢', checkpoint: '◈', offload: '📤', recall: '📥', memory: '🧠' }
const isBlocked = (p) => p.outcome === 'blocked' || p.decision === 'deny'
const isError = (p) => p.outcome === 'error'

// Match a ledger agent_id to a registry entry (exact id, then name) so the
// agent node can show its A2A card. Best-effort — an unmatched agent still renders.
const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '')
function findRegistryEntry(agentId, agents) {
  if (!agentId || !Array.isArray(agents)) return null
  return (
    agents.find((a) => a.id === agentId) ||
    agents.find((a) => norm(a.name) === norm(agentId)) ||
    agents.find((a) => norm(a.id) === norm(agentId)) ||
    null
  )
}

// ── graph construction (pure) ─────────────────────────────────────────────────
function buildFlow({ run, agents, workingSet, staleReport, selectedIdx }) {
  const steps = run?.nodes || []
  const nodes = []
  const edges = []
  if (!steps.length) return { nodes, edges }

  // Working-set / stale lookups by row_hash (the node's stable id).
  const carried = new Set((workingSet?.selected || []).map((s) => s?.node?.row_hash).filter(Boolean))
  const staleBy = new Map(
    (staleReport?.nodes || [])
      .filter((s) => s?.node?.row_hash)
      .map((s) => [s.node.row_hash, s.stale_reasons || []]),
  )
  const overlayOn = !!(workingSet || staleReport)
  const resumeAnchor = workingSet?.resume_pointer || ''

  // ── agent lane (with the A2A AgentCard from the live registry) ──────────────
  const agentIds = [...new Set(steps.map((n) => n.agent_id).filter(Boolean))]
  const firstStepIdxOf = {}
  steps.forEach((n, i) => {
    if (n.agent_id && firstStepIdxOf[n.agent_id] === undefined) firstStepIdxOf[n.agent_id] = i
  })
  agentIds.forEach((aid, i) => {
    const entry = findRegistryEntry(aid, agents)
    nodes.push({
      id: `agent:${aid}`,
      type: 'agent',
      position: { x: i * AGENT_X, y: 0 },
      data: {
        agentId: aid,
        name: entry?.name || aid,
        card: entry?.card_summary || null,
        skills: entry?.card_summary?.skills || entry?.skills || [],
        risk: entry?.authoritative_risk || entry?.risk || '',
        status: entry?.status || '',
      },
      draggable: true,
    })
  })

  // ── the step spine (numbered, wraps into rows, reads like text) ─────────────
  const posOfStep = (i) => ({
    x: (i % PER_ROW) * COL_X,
    y: STEPS_Y + Math.floor(i / PER_ROW) * ROW_H,
  })

  steps.forEach((n, i) => {
    const p = n.payload || {}
    const dimmed = selectedIdx != null && i > selectedIdx
    const ws = !overlayOn
      ? null
      : carried.has(n.row_hash)
        ? { state: 'carried' }
        : staleBy.has(n.row_hash)
          ? { state: 'stale', reasons: staleBy.get(n.row_hash) }
          : { state: 'dropped' }
    nodes.push({
      id: `step:${i}`,
      type: 'step',
      position: posOfStep(i),
      data: {
        idx: i + 1,
        node: n,
        payload: p,
        blocked: isBlocked(p),
        error: isError(p),
        multiAgent: agentIds.length > 1,
        selected: selectedIdx === i,
        dimmed,
        ws,
        isResume: !!resumeAnchor && n.resume_pointer === resumeAnchor,
      },
      draggable: true,
    })

    // next-step arrow (the directed spine — this IS the flow direction)
    if (i > 0) {
      const onTrail = selectedIdx != null && i <= selectedIdx
      const dimEdge = selectedIdx != null && i > selectedIdx
      edges.push({
        id: `next:${i}`,
        source: `step:${i - 1}`,
        target: `step:${i}`,
        sourceHandle: 'out',
        targetHandle: 'in',
        type: 'smoothstep',
        animated: !dimEdge,
        style: {
          stroke: dimEdge ? COLORS.spineDim : COLORS.spine,
          strokeWidth: onTrail ? 2.6 : 1.8,
          opacity: dimEdge ? 0.4 : 1,
        },
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: dimEdge ? COLORS.spineDim : COLORS.spine },
      })
    }

    // ── per-step attachments: Tool / Model / Policy / Error ──────────────────
    const attach = []
    if (p.tool) attach.push({ kind: 'tool', key: `tool:${p.tool}`, label: p.tool, edgeType: 'uses', color: COLORS.tool })
    if (p.model) attach.push({ kind: 'model', key: `model:${p.model}`, label: p.model, edgeType: 'model', color: COLORS.model })
    if (isBlocked(p)) {
      const pol = p.mode || 'denied'
      attach.push({ kind: 'policy', key: `policy:${pol}`, label: pol, edgeType: 'blocked by', color: COLORS.policy })
    }
    if (isError(p) && p.error)
      attach.push({ kind: 'error', key: `error:${i}`, label: String(p.error), edgeType: 'raised', color: COLORS.error })

    attach.forEach((a, k) => {
      // Tools / models / policies are deduped across the run — a shared tool
      // shows convergence (several steps → one tool), which is the directed
      // tool-call graph. First user decides where it sits.
      if (!nodes.some((x) => x.id === a.key)) {
        const base = posOfStep(i)
        nodes.push({
          id: a.key,
          type: 'attachment',
          position: {
            x: base.x + 14 + (k - (attach.length - 1) / 2) * ATTACH_DX,
            y: base.y + ATTACH_DY,
          },
          data: { kind: a.kind, label: a.label, dimmed },
          draggable: true,
        })
      }
      edges.push({
        id: `${a.kind}:${i}:${k}`,
        source: `step:${i}`,
        target: a.key,
        sourceHandle: 'down',
        targetHandle: 'top',
        type: 'smoothstep',
        label: a.edgeType,
        labelStyle: { fontSize: 9, fill: '#64748b' },
        labelBgStyle: { fill: '#f8fafc', opacity: 0.85 },
        style: { stroke: a.color, strokeWidth: 1.4, strokeDasharray: '4 3', opacity: dimmed ? 0.25 : 0.9 },
        markerEnd: { type: MarkerType.ArrowClosed, width: 13, height: 13, color: a.color },
      })
    })
  })

  // agent → its first step ("who starts this trail")
  agentIds.forEach((aid) => {
    const i = firstStepIdxOf[aid]
    if (i === undefined) return
    edges.push({
      id: `starts:${aid}`,
      source: `agent:${aid}`,
      target: `step:${i}`,
      sourceHandle: 'down',
      targetHandle: 'in',
      type: 'smoothstep',
      label: 'runs',
      labelStyle: { fontSize: 9, fill: '#64748b' },
      labelBgStyle: { fill: '#f8fafc', opacity: 0.85 },
      style: { stroke: COLORS.start, strokeWidth: 1.6, strokeDasharray: '6 4' },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: COLORS.start },
    })
  })

  return { nodes, edges }
}

// ── custom nodes ──────────────────────────────────────────────────────────────

function AgentNode({ data }) {
  const skills = data.skills || []
  const shown = skills.slice(0, 5)
  return (
    <div className="w-[240px] rounded-xl border-2 border-brand-600 bg-white shadow-card">
      <div className="flex items-center gap-2 rounded-t-[10px] bg-brand-600 px-3 py-2">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-white/20 text-[11px] font-bold text-white">
          {String(data.name || '?').slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0">
          <div className="truncate text-[12px] font-bold text-white">{data.name}</div>
          <div className="truncate font-mono text-[9px] text-brand-100">{data.agentId}</div>
        </div>
      </div>
      <div className="space-y-1.5 px-3 py-2">
        {data.card?.description && (
          <p className="line-clamp-2 text-[10px] leading-snug text-slate-500">{data.card.description}</p>
        )}
        <div className="flex flex-wrap items-center gap-1">
          <Badge tone={data.card ? 'green' : 'slate'} className="!text-[9px] !px-1.5">
            {data.card ? 'A2A card' : 'no A2A card'}
          </Badge>
          {data.risk && <StatusBadge value={data.risk} title="risk tier" />}
        </div>
        {shown.length > 0 && (
          <div className="flex flex-wrap gap-1 border-t border-slate-100 pt-1.5">
            {shown.map((s) => (
              <span key={s} className="rounded bg-brand-50 px-1.5 py-0.5 font-mono text-[9px] text-brand-700">
                {s}
              </span>
            ))}
            {skills.length > shown.length && (
              <span className="px-1 py-0.5 text-[9px] text-slate-400">+{skills.length - shown.length} skills</span>
            )}
          </div>
        )}
      </div>
      <Handle id="down" type="source" position={Position.Bottom} className="!bg-brand-600" />
    </div>
  )
}

function StepNode({ data }) {
  const { idx, node, payload: p } = data
  const kind = stepKind(node.step)
  const icon = STEP_ICONS[kind] || '•'
  const tone = data.blocked ? 'border-rose-400' : data.error ? 'border-orange-400' : 'border-slate-200'
  const ring = data.selected
    ? 'ring-2 ring-brand-600 ring-offset-2'
    : data.ws?.state === 'carried'
      ? 'ring-2 ring-brand-400'
      : ''
  const tokens = (Number(p.input_tokens) || 0) + (Number(p.output_tokens) || 0)
  return (
    <div
      className={`w-[200px] rounded-xl border-2 bg-white shadow-card transition ${tone} ${ring}`}
      style={{ opacity: data.dimmed ? 0.35 : data.ws?.state === 'stale' ? 0.55 : 1 }}
    >
      <div className="flex items-center gap-2 border-b border-slate-100 px-2.5 py-1.5">
        <span
          className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white ${
            data.blocked ? 'bg-rose-500' : data.error ? 'bg-orange-500' : 'bg-brand-500'
          }`}
        >
          {idx}
        </span>
        <span className="truncate font-mono text-[11px] font-semibold text-brand-900" title={node.step}>
          {icon} {stepLabel(node.step)}
        </span>
      </div>
      <div className="space-y-1 px-2.5 py-1.5">
        <div className="flex flex-wrap items-center gap-1">
          {data.blocked ? (
            <Badge tone="red" className="!text-[9px] !px-1.5">⛔ blocked</Badge>
          ) : data.error ? (
            <Badge tone="amber" className="!text-[9px] !px-1.5">⚠ error</Badge>
          ) : (
            <Badge tone="green" className="!text-[9px] !px-1.5">✓ allowed</Badge>
          )}
          {p.risk && <StatusBadge value={p.risk} title="risk tier" />}
          {data.isResume && (
            <Badge tone="blue" className="!text-[9px] !px-1.5" title="working-set resume anchor">⚓ resume</Badge>
          )}
        </div>
        <div className="flex items-center justify-between text-[9px] text-slate-400">
          <span>
            {kind === 'offload' && p.tokens
              ? `${fmtInt(p.tokens)} tok offloaded`
              : tokens > 0
                ? `${fmtInt(tokens)} tok`
                : data.multiAgent
                  ? node.agent_id
                  : kind}
          </span>
          <span>{timeAgo(node.ts)}</span>
        </div>
        {data.ws?.state === 'stale' && (
          <div className="text-[9px] text-slate-400" title={(data.ws.reasons || []).join(', ')}>
            stale: {(data.ws.reasons || []).join(', ') || '—'}
          </div>
        )}
      </div>
      <Handle id="in" type="target" position={Position.Left} className="!bg-brand-500" />
      <Handle id="out" type="source" position={Position.Right} className="!bg-brand-500" />
      <Handle id="down" type="source" position={Position.Bottom} className="!bg-slate-300" />
    </div>
  )
}

const ATTACH_STYLES = {
  tool: { icon: '⚒', cls: 'border-amber-300 bg-amber-50 text-amber-800' },
  model: { icon: '✦', cls: 'border-brand-300 bg-brand-50 text-brand-700' },
  policy: { icon: '🛡', cls: 'border-rose-300 bg-rose-50 text-rose-700' },
  error: { icon: '⚠', cls: 'border-orange-300 bg-orange-50 text-orange-800' },
}

function AttachmentNode({ data }) {
  const s = ATTACH_STYLES[data.kind] || ATTACH_STYLES.tool
  return (
    <div
      className={`max-w-[150px] rounded-lg border px-2.5 py-1.5 shadow-sm ${s.cls}`}
      style={{ opacity: data.dimmed ? 0.3 : 1 }}
      title={`${data.kind}: ${data.label}`}
    >
      <div className="truncate font-mono text-[10px] font-semibold">
        {s.icon} {data.label}
      </div>
      <div className="text-[8px] uppercase tracking-wider opacity-60">{data.kind}</div>
      <Handle id="top" type="target" position={Position.Top} className="!bg-slate-300" />
    </div>
  )
}

const NODE_TYPES = { agent: AgentNode, step: StepNode, attachment: AttachmentNode }

const MINIMAP_COLORS = { agent: '#2f6b45', step: '#7fbd93', attachment: '#e2e8f0' }

// ── legend + working-set summary ─────────────────────────────────────────────

function Legend({ overlayOn }) {
  const items = [
    ['bg-brand-600', 'Agent (A2A card)'],
    ['bg-brand-500', 'Step (allowed)'],
    ['bg-rose-500', 'Step (blocked)'],
    ['bg-amber-400', 'Tool'],
    ['bg-brand-400', 'Model'],
    ['bg-rose-300', 'Policy'],
    ['bg-orange-400', 'Error'],
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
      {items.map(([dot, label]) => (
        <span key={label} className="flex items-center gap-1">
          <span className={`h-2 w-2 rounded-full ${dot}`} />
          {label}
        </span>
      ))}
      <span className="flex items-center gap-1">
        <span className="inline-block h-px w-5 bg-brand-600" style={{ boxShadow: '0 0 0 0.5px #2f6b45' }} />
        next step →
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-px w-5 border-t border-dashed border-slate-400" />
        uses / blocked by
      </span>
      {overlayOn && (
        <>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-sm ring-2 ring-brand-400" /> carried on resume
          </span>
          <span className="flex items-center gap-1 opacity-50">▢ stale / dropped</span>
        </>
      )}
      <span className="text-slate-300">· click a step to backtrack its trail</span>
    </div>
  )
}

function WorkingSetBar({ ws }) {
  if (!ws) return null
  const pct = ws.budget_tokens > 0 ? Math.min(100, Math.round((ws.used_tokens / ws.budget_tokens) * 100)) : 0
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-slate-200 bg-brand-50 px-3 py-2 text-[11px] text-slate-600">
      <span className="font-semibold text-brand-700">Recovery working set</span>
      <span>✓ {ws.selected?.length ?? 0} carried</span>
      <span>▢ {ws.excluded_stale ?? 0} stale</span>
      <span>↓ {ws.dropped ?? 0} over budget</span>
      {(ws.memory?.length ?? 0) > 0 && <span>🧠 {ws.memory.length} from long-term memory</span>}
      <span className="flex items-center gap-1.5">
        context budget
        <span className="inline-block h-1.5 w-24 overflow-hidden rounded-full bg-brand-100">
          <span className="block h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
        </span>
        {fmtInt(ws.used_tokens)} / {fmtInt(ws.budget_tokens)} tok
      </span>
      {ws.resume_pointer && (
        <span className="font-mono text-[10px] text-brand-700" title="resume anchor">
          ⚓ {ws.resume_pointer}
        </span>
      )}
    </div>
  )
}

// ── backtrack detail panel (the evidence-ledger receipt for one step) ─────────

function HashLine({ label, value, tone = 'text-slate-600' }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
      <span className="w-28 shrink-0 text-slate-400">{label}</span>
      <span className={`select-all break-all font-mono ${tone}`}>{value || '—'}</span>
    </div>
  )
}

function BacktrackPanel({ step, idx, total, onClose }) {
  if (!step) return null
  const p = step.payload || {}
  const blocked = isBlocked(p)
  return (
    <div className="rounded-xl border border-brand-200 bg-white p-4 shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-full bg-brand-600 text-[11px] font-bold text-white">
            {idx + 1}
          </span>
          <span className="font-mono text-sm font-bold text-brand-900">{step.step}</span>
          {blocked && <Badge tone="red">blocked</Badge>}
          {p.decision && <StatusBadge value={p.decision} />}
          {p.risk && <StatusBadge value={p.risk} title="risk tier" />}
          <span className="text-[11px] text-slate-400">
            step {idx + 1} of {total} · {timeAgo(step.ts)}
          </span>
        </div>
        <button onClick={onClose} className="rounded px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-100">
          ✕ close
        </button>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-1 text-[11px] sm:grid-cols-2">
        <HashLine label="Agent" value={step.agent_id} />
        <HashLine label="Task" value={step.task_id} />
        <HashLine label="Actor" value={step.actor} />
        <HashLine label="Workflow" value={step.workflow_id} />
        {p.tool && <HashLine label="Tool" value={p.tool} tone="text-amber-700" />}
        {p.model && <HashLine label="Model" value={p.model} tone="text-brand-700" />}
        {(Number(p.input_tokens) > 0 || Number(p.output_tokens) > 0) && (
          <HashLine
            label="Tokens (laminar)"
            value={`${fmtInt(p.input_tokens)} in · ${fmtInt(p.output_tokens)} out`}
          />
        )}
        {blocked && p.reason && <HashLine label="Deny reason" value={p.reason} tone="text-rose-700" />}
      </div>

      {typeof p.content === 'string' && p.content && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Offloaded content{p.tokens ? ` (~${fmtInt(p.tokens)} tok${p.truncated ? ', truncated' : ''})` : ''}
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-[11px] text-slate-600">
            {p.content}
          </pre>
        </div>
      )}

      <div className="mt-3 space-y-1 border-t border-slate-100 pt-2 text-[11px]">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Evidence ledger (backtrack chain)
        </div>
        <HashLine label="Resume pointer" value={step.resume_pointer} tone="text-brand-700" />
        <HashLine label="Context hash" value={step.context_hash} tone="text-brand-700" />
        <HashLine label="Prev hash 🔗" value={step.prev_hash || '(genesis — chain start)'} />
        <HashLine label="Row hash" value={step.row_hash} />
      </div>
    </div>
  )
}

// ── the component ─────────────────────────────────────────────────────────────

export default function ContextFlowGraph({ run, agents, workingSet, staleReport }) {
  const isDark = useIsDark()
  const [selectedIdx, setSelectedIdx] = useState(null)
  const [rfNodes, setNodes, onNodesChange] = useNodesState([])
  const [rfEdges, setEdges, onEdgesChange] = useEdgesState([])

  const built = useMemo(
    () => buildFlow({ run, agents, workingSet, staleReport, selectedIdx }),
    [run, agents, workingSet, staleReport, selectedIdx],
  )

  // Apply the built graph only when it actually changed. The page polls every
  // few seconds and rebuilds identical objects — pushing those into React Flow
  // every time would strip its measured node sizes (hiding nodes until they
  // re-measure) and discard drag positions. On a real change, surviving nodes
  // keep their position and measured dims so nothing flashes or jumps.
  const sigRef = useRef('')
  useEffect(() => {
    const sig = JSON.stringify([built.nodes, built.edges])
    if (sig === sigRef.current) return
    sigRef.current = sig
    setNodes((prev) => {
      const prevById = new Map(prev.map((n) => [n.id, n]))
      return built.nodes.map((n) => {
        const p = prevById.get(n.id)
        return p ? { ...n, position: p.position, measured: p.measured } : n
      })
    })
    setEdges(built.edges)
  }, [built, setNodes, setEdges])

  const steps = run?.nodes || []
  const totalTokens = useMemo(
    () =>
      steps.reduce(
        (acc, n) => {
          acc.in += Number(n.payload?.input_tokens) || 0
          acc.out += Number(n.payload?.output_tokens) || 0
          return acc
        },
        { in: 0, out: 0 },
      ),
    [steps],
  )

  if (!steps.length)
    return (
      <p className="px-5 py-12 text-center text-sm text-slate-400">
        No governed steps recorded in this run yet.
      </p>
    )

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Legend overlayOn={!!(workingSet || staleReport)} />
        {(totalTokens.in > 0 || totalTokens.out > 0) && (
          <span className="text-[10px] text-slate-400" title="token spend metered across this run (laminar)">
            Σ run tokens: {fmtInt(totalTokens.in)} in · {fmtInt(totalTokens.out)} out
          </span>
        )}
      </div>

      <WorkingSetBar ws={workingSet} />

      <div className="h-[520px] overflow-hidden rounded-xl border border-slate-200 bg-slate-50/70">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={NODE_TYPES}
          colorMode={isDark ? 'dark' : 'light'}
          fitView
          fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
          minZoom={0.15}
          nodesConnectable={false}
          deleteKeyCode={null}
          onNodeClick={(_, node) => {
            if (node.type === 'step') {
              const i = Number(node.id.split(':')[1])
              setSelectedIdx((s) => (s === i ? null : i))
            }
          }}
          onPaneClick={() => setSelectedIdx(null)}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={18}
            size={1}
            color={isDark ? '#2b3a32' : '#cbd5e1'}
          />
          <Controls position="bottom-left" showInteractive={false} />
          {steps.length > PER_ROW * 2 && (
            <MiniMap
              pannable
              zoomable
              position="bottom-right"
              nodeColor={(n) => MINIMAP_COLORS[n.type] || '#e2e8f0'}
            />
          )}
        </ReactFlow>
      </div>

      {selectedIdx != null && steps[selectedIdx] && (
        <BacktrackPanel
          step={steps[selectedIdx]}
          idx={selectedIdx}
          total={steps.length}
          onClose={() => setSelectedIdx(null)}
        />
      )}
    </div>
  )
}
