import { useEffect, useRef, useState } from 'react'
import { EXPORT_FORMATS, exportResult } from '../export.js'

// Per-result export control: a small "Export ▾" button that opens a menu of
// formats (PDF / Word / Excel / Text / Markdown). Renders nothing until there's
// a result to export. The format list is data-driven, so adding a format is a
// one-line change in export.js.
export default function ExportMenu({ result, title }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const onEsc = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  if (!result) return null

  return (
    <div className="export-menu" ref={ref}>
      <button
        type="button"
        className="export-btn"
        onClick={() => setOpen((o) => !o)}
        title="Export this result"
      >
        ⬇ Export <span className="ex-caret" aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="export-pop" role="menu">
          {EXPORT_FORMATS.map((f) => (
            <button
              type="button"
              key={f.key}
              role="menuitem"
              className="export-item"
              onClick={() => {
                exportResult(f.key, result, title)
                setOpen(false)
              }}
            >
              <span className="ex-label">{f.label}</span>
              <span className="ex-hint">{f.ext}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
