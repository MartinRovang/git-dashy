import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Detail, Row } from '../types'
import { tone } from '../tokens'

type Act = [key: string, cls: string, label: string, note: string, off: boolean, act: string]

/** Everything you can do to one PR, in the order you reach for it. One list, three surfaces: the
 *  pane's Options button, a right-click on a row, and the keys in handleKey.
 *
 *  `d` is the detail of the SELECTED PR, so a right-click on some other row passes null and loses
 *  only the rows that need it — the full review, and the team the bind would use. */
function acts(p: Row, d: Detail | null): Act[] {
  const out: Act[] = []
  const rr = p.section === 'REVIEW REQUESTED'
  const mine = p.section === 'MINE'
  const reviewed = !!(p.review && !p.busy && tone(p.review))
  if (rr)
    out.push(['r', 'go', p.busy ? 'Reviewing…' : reviewed ? 'Reviewed' : 'Review this PR', '', p.busy || reviewed, 'review'])
  if (mine)
    out.push([
      'p',
      '',
      p.pre?.moved ? 'Re-run the pre-review' : p.pre ? 'Read the pre-review' : 'Pre-review',
      p.pre?.moved ? 'the PR moved since' : 'nothing posted',
      p.busy,
      'pre',
    ])
  if (d?.review) out.push(['v', '', 'Read the full review', d.review.model, false, 'view'])
  out.push(['2', '', 'View the code', 'diff and marks', false, 'code'])
  out.push(['o', '', 'Open in browser', 'github', false, 'open'])
  out.push(['y', '', 'Copy the URL', 'clipboard', false, 'copy'])
  if (mine) out.push(['+', '', 'Request a review', 'pick a collaborator', false, 'reviewer'])
  out.push(['b', '', 'Bind the repo to a team', d?.brief?.whose || '', false, 'bind'])
  out.push(['n', '', "Edit this repo's memory", (p.repo || '').split('/').pop() || '', false, 'memory'])
  return out
}

export type Anchor = { x: number; y: number }

/** The same list as a popup. Closes on Escape, on a click anywhere else, and on a pick.
 *
 * ponytail: the Escape listener captures and stops the event, because the board's own handler reads
 * Escape as "open the menu" — without that, dismissing this would open that. */
export function ActsMenu({
  p,
  d,
  at,
  onAct,
  onClose,
}: {
  p: Row
  d: Detail | null
  at: Anchor
  onAct: (name: string, target: Row) => void
  onClose: () => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<Anchor>(at)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const { width, height } = el.getBoundingClientRect()
    const pad = 8
    setPos({
      x: Math.max(pad, Math.min(at.x, window.innerWidth - width - pad)),
      y: Math.max(pad, Math.min(at.y, window.innerHeight - height - pad)),
    })
  }, [at])

  useEffect(() => {
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) onClose()
    }
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      onClose()
    }
    window.addEventListener('pointerdown', away, true)
    window.addEventListener('keydown', key, true)
    window.addEventListener('resize', onClose)
    return () => {
      window.removeEventListener('pointerdown', away, true)
      window.removeEventListener('keydown', key, true)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  return (
    <div className="acts" ref={box} role="menu" style={{ left: pos.x, top: pos.y }}>
      <div className="actsh">
        <span className="mono">#{p.number}</span>
        <span>{p.repo}</span>
      </div>
      {acts(p, d).map(([k, cls, label, note, off, act]) => (
        <button
          key={act}
          role="menuitem"
          className={`ai${cls ? ` ${cls}` : ''}`}
          disabled={off}
          onClick={() => {
            onClose()
            onAct(act, p)
          }}
        >
          <kbd className="hint">{k}</kbd>
          <b>{label}</b>
          <em>{note}</em>
        </button>
      ))}
    </div>
  )
}
