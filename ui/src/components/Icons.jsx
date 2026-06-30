// Lightweight inline stroke icons (Lucide-style) so the nav + cards share one
// clean line-icon look without pulling in an icon dependency. Every glyph is a
// 24×24 stroked path that inherits `currentColor`, so colour is set by the caller.

const PATHS = {
  // ── nav ──────────────────────────────────────────────────────────────────
  dashboard: (
    <>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.6" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6" />
    </>
  ),
  radar: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 12l6.2-3.6" />
      <circle cx="12" cy="12" r="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  approvals: (
    <>
      <path d="M12 3l7 2.6v5.1c0 4.4-3 7-7 8.3-4-1.3-7-3.9-7-8.3V5.6z" />
      <path d="M9 11.8l2.1 2.1 4-4.2" />
    </>
  ),
  workflow: (
    <>
      <circle cx="6" cy="6" r="2.4" />
      <circle cx="6" cy="18" r="2.4" />
      <circle cx="18" cy="12" r="2.4" />
      <path d="M8.3 7.1l7.5 3.8M8.3 16.9l7.5-3.8" />
    </>
  ),
  context: (
    <>
      <circle cx="12" cy="12" r="2.4" />
      <circle cx="5" cy="5.5" r="2" />
      <circle cx="19" cy="5.5" r="2" />
      <circle cx="5" cy="18.5" r="2" />
      <circle cx="19" cy="18.5" r="2" />
      <path d="M10.3 10.5L6.4 7M13.7 10.5L17.6 7M10.3 13.5L6.4 17M13.7 13.5L17.6 17" />
    </>
  ),
  tokens: <path d="M3 12h3.5l2.5 7 4-14 2.5 7H21" />,
  hooks: (
    <>
      <circle cx="12" cy="5" r="2.3" />
      <path d="M12 7.3V21M5 12a7 7 0 0014 0M5 12H2.8m16.2 0H21" />
    </>
  ),
  policy: (
    <>
      <path d="M12 3v18M6 21h12M5.5 7h13" />
      <path d="M5.5 7L3 12.2a2.5 2.5 0 005 0zM18.5 7L16 12.2a2.5 2.5 0 005 0z" />
    </>
  ),
  sandbox: (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2.2" />
      <path d="M7 9.5l2.6 2.5L7 14.5M12.5 14.5H17" />
    </>
  ),
  // ── chrome ─────────────────────────────────────────────────────────────────
  arrowUpRight: <path d="M7 17L17 7M8 7h9v9" />,
  menu: <path d="M4 6.5h16M4 12h16M4 17.5h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  panelLeft: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2.2" />
      <path d="M9 4v16" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="3.8" />
      <path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4l1.4-1.4M18 6l1.4-1.4" />
    </>
  ),
  moon: <path d="M20.5 13.3A8.4 8.4 0 1110.7 3.5a6.6 6.6 0 009.8 9.8z" />,
  bell: (
    <>
      <path d="M6 9a6 6 0 0112 0c0 3.6 1.2 5 2 6H4c.8-1 2-2.4 2-6z" />
      <path d="M10.2 20a2 2 0 003.6 0" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20.5 20.5L16.5 16.5" />
    </>
  ),
}

export function Icon({ name, className = 'h-5 w-5', strokeWidth = 1.8 }) {
  const body = PATHS[name]
  if (!body) return null
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {body}
    </svg>
  )
}
