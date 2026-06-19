// Small display helpers — pure functions, no app state.

export const fmtInt = (n) => Number(n ?? 0).toLocaleString('en-US')

// Token cost — the gateway reports 0.0 for local Ollama models; keep 4 decimals
// so the column matches the backend (e.g. "0.0000").
export const fmtCost = (n) => Number(n ?? 0).toFixed(4)

export const shortId = (id, n = 8) => (id ? String(id).slice(0, n) : '—')

// Capped percentage for progress bars (0..100).
export const pctCapped = (ratio) => Math.round(Math.min(Math.max(Number(ratio || 0), 0), 1) * 100)

// True percentage (can exceed 100 when an agent blew past its budget).
export const pctReal = (ratio) => Math.round(Number(ratio || 0) * 100)

export function timeAgo(ts) {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function fmtDuration(sec) {
  if (sec == null || Number.isNaN(sec)) return '—'
  if (sec < 1) return '<1s'
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

// Turn an event kind like "token_hard_stop" into "Token Hard Stop".
export function prettyKind(kind) {
  return String(kind || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim()
}
