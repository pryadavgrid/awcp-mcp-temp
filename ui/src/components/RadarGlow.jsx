// A soft, working radar that sits BEHIND the hero heading — between the smoke
// background and the text. Concentric rings + crosshair, a slowly rotating sweep,
// and pulse rings that radiate outward. Kept low-opacity so it never overpowers the
// smoke or the copy, and it's tuned in brand green for both light and dark themes.
// Keyframes are uniquely named + scoped in a local <style>, so nothing else is
// affected. pointer-events:none and aria-hidden — purely decorative.
export function RadarGlow() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute left-1/2 top-1/2 z-0 h-[min(86vw,600px)] w-[min(86vw,600px)] -translate-x-1/2 -translate-y-1/2"
    >
      <style>{`
        @keyframes awcp-radar-spin { to { transform: rotate(360deg); } }
      `}</style>

      {/* soft green centre glow — a green core that fades out, kept gentle so it
          never overpowers the smoke or the hero copy */}
      <div
        className="absolute inset-0 rounded-full"
        style={{
          background:
            'radial-gradient(circle, rgba(74,222,128,0.22) 0%, rgba(69,176,106,0.10) 42%, transparent 66%)',
        }}
      />

      {/* static rings + crosshair */}
      <svg
        viewBox="0 0 200 200"
        className="absolute inset-0 h-full w-full text-[#3a9d5f]/35 dark:text-[#5ec888]/40"
      >
        <g fill="none" stroke="currentColor" strokeWidth="0.5">
          <circle cx="100" cy="100" r="28" />
          <circle cx="100" cy="100" r="52" />
          <circle cx="100" cy="100" r="76" />
          <circle cx="100" cy="100" r="98" />
          <line x1="100" y1="2" x2="100" y2="198" />
          <line x1="2" y1="100" x2="198" y2="100" />
        </g>
      </svg>

      {/* rotating sweep (clipped to the outer ring) */}
      <div
        className="absolute inset-0 rounded-full"
        style={{
          background:
            'conic-gradient(from 0deg, rgba(69,176,106,0.28), rgba(69,176,106,0) 55deg, transparent 100%)',
          animation: 'awcp-radar-spin 6s linear infinite',
          WebkitMaskImage: 'radial-gradient(circle, #000 0 98%, transparent 99%)',
          maskImage: 'radial-gradient(circle, #000 0 98%, transparent 99%)',
        }}
      />
    </div>
  )
}
