import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { StateData } from './types'

/** Poll /api/state on an interval, plus a manual reload for mutations. The one lifecycle effect. */
export function useStatePoll(ms: number): [StateData | null, () => void] {
  const [data, setData] = useState<StateData | null>(null)
  const mounted = useRef(true)
  const last = useRef('')
  const reload = useCallback(async () => {
    try {
      const r = await api('/api/state')
      if (!r.ok) return
      const text = await r.text()
      // the same payload keeps the same object, so nothing downstream re-renders (#71)
      if (text === last.current || !mounted.current) return
      const got = JSON.parse(text) as StateData // before remembering it: a body that fails to parse is not seen
      last.current = text
      setData(got)
    } catch {
      /* server gone; the next tick retries */
    }
  }, [])
  useEffect(() => {
    mounted.current = true
    reload()
    // a hidden window asks nothing; showing it again asks at once
    const id = setInterval(() => document.hidden || reload(), ms)
    const shown = () => document.hidden || reload()
    document.addEventListener('visibilitychange', shown)
    return () => {
      mounted.current = false
      clearInterval(id)
      document.removeEventListener('visibilitychange', shown)
    }
  }, [reload, ms])
  return [data, reload]
}

/** A ticking clock for the "refresh in Ns" countdown and running-review elapsed labels. `ms` 0 stops it.
 *  Call it in the component that shows the time, not above it: every tick re-renders the caller's subtree (#72). */
export function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!ms) return
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}
