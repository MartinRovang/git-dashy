// The data half of the review screens: read a held review or a pre-review, poll while the agent works, and
// act on it. ReviewTalk renders what this returns and fetches nothing itself.
import { useEffect, useRef, useState } from 'react'
import { api, errorText, post } from './api'
import type { Talk } from './types'

/** Which saved review a screen is about. A held review is keyed by repo and number, the way ~/.prs_held
 *  names it; a pre-review by its PR's URL, the way /api/prereview finds it. */
export type Source = { kind: 'held'; repo: string; number: number } | { kind: 'pre'; url: string }

/** What the screen draws, whichever store it came from. */
export type View = { text: string; verdict: string; moved: boolean; talk: Talk | null }

async function fetchView(src: Source): Promise<View | string> {
  const r =
    src.kind === 'held'
      ? await api(`/api/posting?repo=${encodeURIComponent(src.repo)}&number=${src.number}`)
      : await api(`/api/prereview?url=${encodeURIComponent(src.url)}`)
  if (!r.ok) return errorText(r)
  const d = await r.json()
  if (src.kind === 'held') {
    if (!d.held) return 'nothing is waiting on this PR any more'
    return { text: d.held.body, verdict: d.held.verdict, moved: d.held.moved, talk: d.held.talk }
  }
  return { text: d.text, verdict: d.talk?.verdict || '', moved: d.moved, talk: d.talk }
}

/** The saved review and a way to act on it.
 *
 *  ponytail: one poll, scoped to the open screen, not the board's. The agent answers in minutes and the board
 *  refreshes on a much longer interval; while the review is `busy` this asks every 1.5s and stops the moment
 *  the turn lands, and closing the screen stops it with the component. */
export function useReviewTalk(source: Source, onFlash: (s: string) => void, onText?: (t: string) => void) {
  const [view, setView] = useState<View | null>(null)
  const [failed, setFailed] = useState('')
  const held = source.kind === 'held'
  const key = held ? `${source.repo}#${source.number}` : source.url

  // Which PR the screen is on now. Every load remembers the key it was built for and drops its reply if
  // that is no longer it, so a fetch still in the air when the screen switches PRs cannot write the OLD
  // review over the new one.
  //
  // ponytail: NOT one `alive` ref flipped in the effect's cleanup. The outgoing cleanup sets it false and
  // the incoming effect sets it true again in the same commit, so the old request finds it true and lands
  // anyway -- that guards unmount and nothing else, which is the bug wearing a fix. It is the key because
  // every `load` closure already knows its own, which covers the interval and act()'s reload too; a flag
  // scoped to one effect run would leave both of those unguarded.
  const on = useRef(key)
  const load = async () => {
    const mine = key
    const got = await fetchView(source)
    if (on.current !== mine) return
    if (typeof got === 'string') return setFailed(got)
    setView(got)
    onText?.(got.text)
  }
  useEffect(() => {
    on.current = key
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  const busy = !!view?.talk?.busy
  useEffect(() => {
    if (!busy) return
    const id = setInterval(() => void load(), 1500)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, key])

  /** One of discuss / revise / accept / keep. Resolves to whether the server took it. */
  const act = async (body: Record<string, unknown>, ok?: string) => {
    const where = held ? { repo: source.repo, number: source.number } : { url: source.url }
    const r = await post(held ? '/api/posting' : '/api/prereview', { ...where, ...body })
    if (!r.ok) onFlash(`✗ ${await errorText(r)}`)
    else if (ok) onFlash(ok)
    await load()
    return r.ok
  }
  return { view, failed, act }
}
