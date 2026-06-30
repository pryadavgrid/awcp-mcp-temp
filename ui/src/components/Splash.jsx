import { useEffect, useState } from 'react'
import logoStill from '../assets/awcp-logo-still.png'
import logoAnim from '../assets/awcp-logo-anim.webp'

const RISE_MS = 720 // matches the rise keyframe; the radar starts rotating after it

// Boot splash. Shown over the app on a fresh page load for a short beat so the
// brand mark plays as a loading screen, then App fades it out (the `leaving` prop)
// and unmounts it — revealing the dashboard underneath, which has been mounting
// (and polling) the whole time. Theme-aware via the `dark` class.
//
// Staged entrance: first the alien rises up from the bottom into the circle as a
// STATIC poster (the webp's own first frame), so the radar is still. Once it has
// settled we swap to the animated webp, so the radar only STARTS rotating after
// the alien is in place. The poster is byte-identical to webp frame 0, so the swap
// is invisible; the webp is preloaded so it can't flash. Keyframes are uniquely
// named + scoped here, so nothing else is affected.
export function Splash({ leaving = false }) {
  const [rotating, setRotating] = useState(false)
  useEffect(() => {
    const pre = new Image() // preload the webp so the swap is instant (no flash)
    pre.src = logoAnim
    const t = setTimeout(() => setRotating(true), RISE_MS)
    return () => clearTimeout(t)
  }, [])

  return (
    <div
      className={`fixed inset-0 z-[100] grid place-items-center bg-[#f3f5f3] transition-opacity duration-500 dark:bg-[#0e1512] ${
        leaving ? 'pointer-events-none opacity-0' : 'opacity-100'
      }`}
      aria-hidden={leaving}
      role="status"
    >
      <style>{`
        @keyframes awcp-splash-rise {
          0%   { transform: translateY(64px) scale(0.92); opacity: 0; }
          70%  { transform: translateY(0)    scale(1.04); opacity: 1; }
          100% { transform: translateY(0)    scale(1);    opacity: 1; }
        }
        @keyframes awcp-splash-fade {
          0%   { transform: translateY(8px); opacity: 0; }
          100% { transform: translateY(0);   opacity: 1; }
        }
      `}</style>

      <div className="flex flex-col items-center gap-6">
        <img
          src={rotating ? logoAnim : logoStill}
          alt="AWCP"
          className="h-32 w-32 drop-shadow-[0_10px_28px_rgba(47,107,69,0.25)]"
          style={{ animation: 'awcp-splash-rise 0.72s cubic-bezier(0.22, 1, 0.36, 1) both' }}
        />
        <div
          className="text-center"
          style={{ animation: 'awcp-splash-fade 0.5s ease-out 0.55s both' }}
        >
          <div className="text-3xl font-extrabold tracking-tight text-brand-900">AWCP</div>
          <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-400">
            Control Plane
          </div>
        </div>
        {/* The radar sweep already reads as "loading"; these add a subtle beat. */}
        <div
          className="flex items-center gap-1.5"
          style={{ animation: 'awcp-splash-fade 0.5s ease-out 0.7s both' }}
        >
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
