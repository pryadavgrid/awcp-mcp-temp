import { useCallback, useEffect, useState } from 'react'

// Light / dark theme, persisted to localStorage and applied as a `dark` class on
// <html> (Tailwind's class strategy). The initial value matches the pre-paint
// script in index.html: saved choice wins, else the OS preference.
const KEY = 'awcp-theme'

function initialTheme() {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    /* localStorage unavailable — fall through to system preference */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState(initialTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      /* ignore persistence failures (private mode, etc.) */
    }
  }, [theme])

  const toggle = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [])

  return { theme, toggle, isDark: theme === 'dark' }
}
