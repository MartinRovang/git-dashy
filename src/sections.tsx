import { useState, type DragEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { post } from './api'
import { useFloatBox } from './float'
import type { Layout } from './types'
import { arrange, flip, moved, outside, same } from './layout'

// one save in flight per setting: `serve` runs each request on its own thread, so two quick edits
// could otherwise land in either order and leave the older layout on disk
const queue: Record<string, Promise<unknown>> = {}

/** Sections you can switch off, fold, drag up and down, and drag out of `panel` into a window of their own.
 *
 * ponytail: native HTML drag. A webview that reports no drop point (0,0) cannot pop out; add a button
 * back if that bites. Escape past the panel's edge also pops out: dragend reads the same for a cancel
 * and a drop on nothing. Edits are kept locally and posted quietly, so a drag does not wait on a reload.
 */
export function useSections(setting: 'pane' | 'side', names: string[], saved: Layout | undefined, panel: string) {
  const [mine, setMine] = useState<Layout | null>(null)
  const [dragged, setDragged] = useState<string | null>(null)
  // where the dragged section would land: above or below this one, drawn as a line
  const [at, setAt] = useState<{ k: string; after: boolean } | null>(null)
  const lay = mine || saved || {}
  // once the server holds what we sent, follow the server again, so a change made elsewhere shows up.
  // Set during render, React's pattern for state that follows a prop; an effect would render twice.
  if (mine && same(mine, saved)) setMine(null)
  const put = (next: Layout) => {
    setMine(next)
    queue[setting] = (queue[setting] || Promise.resolve()).then(() =>
      // a refused save drops the local copy, so the panel shows what is actually saved
      post('/api/settings', { [setting]: next }).then(
        (r) => r.ok || setMine(null),
        () => setMine(null),
      ),
    )
  }
  const order = arrange(lay.order, names)
  const move = (from: string, to: string, after: boolean) => put({ ...lay, order: moved(order, from, to, after) })
  const sec = (k: string) => {
    const out = !!lay.out?.includes(k)
    return {
      off: !!lay.off?.includes(k),
      shut: !!lay.shut?.includes(k),
      out,
      /** classes for the wrapper: lifted while in your hand, drop-before/-after where it would land */
      cls: `${out ? ' out' : ''}${dragged === k ? ' lifted' : ''}${at?.k === k && dragged !== k ? (at.after ? ' drop-after' : ' drop-before') : ''}`,
      style: { order: order.indexOf(k) },
      wrap: {
        onDragOver: (e: DragEvent<HTMLElement>) => {
          if (!dragged) return
          e.preventDefault()
          const r = e.currentTarget.getBoundingClientRect()
          const after = e.clientY > r.top + r.height / 2
          if (at?.k !== k || at.after !== after) setAt({ k, after })
        },
        onDrop: (e: DragEvent<HTMLElement>) => {
          e.preventDefault()
          if (dragged && at) move(dragged, at.k, at.after)
          setAt(null)
        },
      },
      head: {
        draggable: true,
        onDragStart: (e: DragEvent<HTMLElement>) => {
          setDragged(k)
          e.dataTransfer.effectAllowed = 'move'
          e.dataTransfer.setData('text/plain', k)
        },
        onDragEnd: (e: DragEvent<HTMLElement>) => {
          setDragged(null)
          setAt(null)
          const r = document.querySelector(panel)?.getBoundingClientRect()
          if (r && outside(r, e.clientX, e.clientY) && !out) put({ ...lay, out: [...(lay.out || []), k] })
        },
      },
      fold: () => put({ ...lay, shut: flip(lay.shut, k) }),
      dock: () => put({ ...lay, out: flip(lay.out, k) }),
    }
  }
  return { lay, order, sec, switchOff: (k: string) => put({ ...lay, off: flip(lay.off, k) }) }
}

/** The chips that switch sections on and off. */
export function SecFilter({ secs, off, onFlip }: { secs: [string, string][]; off: string[] | undefined; onFlip: (k: string) => void }) {
  return (
    <div className="secfilter">
      {secs.map(([name, label]) => (
        <button key={name} className="tag" aria-pressed={!off?.includes(name)} onClick={() => onFlip(name)}>
          {label}
        </button>
      ))}
    </div>
  )
}

/** A section popped out into its own window; ⤓ puts it back. On body, so it outlives its panel being hidden. */
export function Out({ id, label, onDock, children }: { id: string; label: string; onDock: () => void; children: ReactNode }) {
  const { box, el, drag, grip, style } = useFloatBox(
    `dashy-sec-${id}`,
    () => ({ x: Math.max(8, Math.round(window.innerWidth / 2 - 210)), y: 90, w: 420, h: 360, max: false }),
    '.ib',
  )
  return createPortal(
    <div ref={el} className={`fw${box.max ? ' max' : ''}`} style={style} role="dialog" aria-label={label}>
      <div className="bar" title="drag to move, double-click to maximize" {...drag}>
        <span className="lab">{label}</span>
        <div style={{ flex: 1 }} />
        <span className="ib" title="put it back" onClick={onDock}>
          ⤓
        </span>
      </div>
      <div className="in scroll" style={{ padding: '14px 16px' }}>
        {children}
      </div>
      <div className="fgrip" {...grip} />
    </div>,
    document.body,
  )
}
