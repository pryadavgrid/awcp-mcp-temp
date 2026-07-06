// Circular "void" reveal theme toggle — the same effect the main app uses
// (View Transitions API + the `awcp-theme-reveal` keyframes / ::view-transition
// rules in index.css). Extracted so the pre-app screens (Landing, Login) get the
// identical radiate-out transition without duplicating the logic.
//
//   e            — the click event (its target's centre is the reveal origin)
//   currentIsDark — the theme BEFORE toggling
//   apply(next)   — called with the next isDark so the caller can sync React state
const THEME_KEY = 'awcp-theme'

export function toggleThemeReveal(e, currentIsDark, apply) {
  const root = document.documentElement
  const rect = e?.currentTarget?.getBoundingClientRect?.()
  const cx = rect ? rect.left + rect.width / 2 : window.innerWidth / 2
  const cy = rect ? rect.top + rect.height / 2 : 40
  const maxR = Math.hypot(
    Math.max(cx, window.innerWidth - cx),
    Math.max(cy, window.innerHeight - cy),
  )
  root.style.setProperty('--awcp-tx', `${cx}px`)
  root.style.setProperty('--awcp-ty', `${cy}px`)
  root.style.setProperty('--awcp-tr', `${maxR}px`)

  const next = !currentIsDark
  const swap = () => {
    // Toggle the class synchronously so the View Transition captures the new theme.
    root.classList.toggle('dark', next)
    try {
      localStorage.setItem(THEME_KEY, next ? 'dark' : 'light')
    } catch {
      /* ignore persistence failures */
    }
    apply(next)
  }

  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (document.startViewTransition && !reduce) document.startViewTransition(swap)
  else swap()
}
