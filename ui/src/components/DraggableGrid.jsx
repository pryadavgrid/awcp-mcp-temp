import { useEffect, useState } from 'react'

// A drag-to-reorder tile grid. Each tile is `{ id, span, render }` where `span`
// is how many of the 4 columns it occupies (1 or 2). The chosen order is saved
// to localStorage under `storageKey`, so an operator's hand-arranged board
// survives reloads. Reordering is plain HTML5 drag-and-drop — no extra deps.

const SPAN = {
  1: '',
  2: 'sm:col-span-2 xl:col-span-2',
}

// Keep the saved ids that still exist (in their saved order), then append any
// tiles that are new since the order was saved. Guards against a stale layout
// hiding a tile we later added (or crashing on one we removed).
function reconcile(saved, ids) {
  const known = new Set(ids)
  const kept = saved.filter((id) => known.has(id))
  const missing = ids.filter((id) => !kept.includes(id))
  return [...kept, ...missing]
}

function loadOrder(key, ids) {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || 'null')
    if (Array.isArray(raw)) return reconcile(raw, ids)
  } catch {
    /* ignore malformed storage */
  }
  return ids
}

export function DraggableGrid({ storageKey, tiles, className = '' }) {
  const ids = tiles.map((t) => t.id)
  const idsKey = ids.join('|')

  const [order, setOrder] = useState(() => loadOrder(storageKey, ids))
  const [dragId, setDragId] = useState(null)
  const [overId, setOverId] = useState(null)

  // Reconcile if the set of tiles ever changes shape.
  useEffect(() => {
    setOrder((prev) => reconcile(prev, idsKey.split('|')))
  }, [idsKey])

  const byId = new Map(tiles.map((t) => [t.id, t]))
  const ordered = order.map((id) => byId.get(id)).filter(Boolean)
  const customized = order.join('|') !== idsKey

  const persist = (next) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }

  const move = (targetId) => {
    if (!dragId || dragId === targetId) return
    setOrder((prev) => {
      const from = prev.indexOf(dragId)
      const to = prev.indexOf(targetId)
      if (from === -1 || to === -1) return prev
      const next = [...prev]
      next.splice(to, 0, next.splice(from, 1)[0])
      persist(next)
      return next
    })
  }

  const reset = () => {
    setOrder(ids)
    try {
      localStorage.removeItem(storageKey)
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end px-1">
        {customized && (
          <button
            onClick={reset}
            className="text-xs font-semibold text-brand-600 transition hover:text-brand-800"
          >
            Reset layout
          </button>
        )}
      </div>

      <div
        className={`grid grid-cols-1 items-stretch gap-5 sm:grid-cols-2 xl:grid-cols-4 ${className}`}
      >
        {ordered.map((t) => {
          const isDragging = dragId === t.id
          const isOver = overId === t.id && !isDragging
          return (
            <div
              key={t.id}
              draggable
              onDragStart={(e) => {
                setDragId(t.id)
                e.dataTransfer.effectAllowed = 'move'
                // Firefox won't start a drag unless some data is set.
                try {
                  e.dataTransfer.setData('text/plain', t.id)
                } catch {
                  /* ignore */
                }
              }}
              onDragEnter={() => setOverId(t.id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                move(t.id)
                setOverId(null)
              }}
              onDragEnd={() => {
                setDragId(null)
                setOverId(null)
              }}
              className={[
                SPAN[t.span] || SPAN[1],
                'group/tile relative h-full cursor-grab rounded-2xl transition duration-200 active:cursor-grabbing',
                isDragging ? 'scale-[0.98] opacity-40' : '',
                isOver ? 'scale-[1.01]' : '',
              ].join(' ')}
            >
              {/* Drag affordance: a grip that fades in on hover, colored per theme. */}
              <span
                aria-hidden="true"
                className="pointer-events-none absolute left-1/2 top-1 z-20 -translate-x-1/2 select-none text-sm leading-none tracking-tight text-slate-500/70 opacity-0 transition duration-300 group-hover/tile:opacity-100 dark:text-white/70"
              >
                ⠿
              </span>
              {t.render()}
            </div>
          )
        })}
      </div>
    </div>
  )
}
