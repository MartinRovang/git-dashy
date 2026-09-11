import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { StateData } from './types'

/** Poll /api/state on an interval, plus a manual reload for mutations. The one lifecycle effect. */
export function useStatePoll(ms: number): [StateData | null, () => void] {
  const [data, setData] = useState<StateData | null>(null)
  const mounted = useRef(true)
  const reload = useCallback(async () => {
    try {
      const r = await api('/api/state')
      if (!r.ok) return
      const got = (await r.json()) as StateData
      if (mounted.current) setData(got)
    } catch {
      /* server gone; the next tick retries */
    }
  }, [])
  useEffect(() => {
    mounted.current = true
    reload()
    const id = setInterval(reload, ms)
    return () => {
      mounted.current = false
      clearInterval(id)
    }
  }, [reload, ms])
  return [data, reload]
}

/** A ticking clock for the "refresh in Ns" countdown and running-review elapsed labels. */
export function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}
