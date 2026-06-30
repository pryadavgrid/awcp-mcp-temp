import { useEffect, useState } from 'react'

// Reactive CSS media-query match. Used to know when we're on a desktop-width
// viewport so the sidebar can be a static column there but an off-canvas drawer
// on tablet/mobile.
export function useMediaQuery(query) {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  )

  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = (e) => setMatches(e.matches)
    setMatches(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])

  return matches
}
