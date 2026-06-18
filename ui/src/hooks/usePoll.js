import { useCallback, useEffect, useRef, useState } from 'react'
import { POLL_MS } from '../config'

// Poll an async fetcher on an interval. Returns { data, error, loading, refresh }.
// Keeps the last-good data while a refetch is in flight, and surfaces transient
// errors without blanking the view, so a brief gateway hiccup doesn't wipe a table.
export function usePoll(fetcher, deps = [], interval = POLL_MS) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const mounted = useRef(true)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const fn = useCallback(fetcher, deps)

  const refresh = useCallback(async () => {
    try {
      const d = await fn()
      if (mounted.current) {
        setData(d)
        setError(null)
      }
    } catch (e) {
      if (mounted.current) setError(e?.message || String(e))
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [fn])

  useEffect(() => {
    mounted.current = true
    refresh()
    const id = setInterval(refresh, interval)
    return () => {
      mounted.current = false
      clearInterval(id)
    }
  }, [refresh, interval])

  return { data, error, loading, refresh }
}
