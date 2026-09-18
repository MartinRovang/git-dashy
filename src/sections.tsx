import { useState, type DragEvent, type ReactNode } from 'react'
import { post } from './api'
import { useFloatBox } from './float'

/** A panel's layout: each list names sections. Saved as a setting, since the GUI's localStorage starts empty every launch. */
export type Layout = { order?: string[]; off?: string[]; shut?: string[]; out?: string[] }

const flip = (l: string[] = [], v: string) => (l.includes(v) ? l.filter((x) => x !== v) : [...l, v])

/** Sections you can switch off, fold, drag up and down, and drag out of `panel` into a window of their own.
 *
 * ponytail: native HTML drag. A webview that reports no drop point (0,0) cannot pop out; add a button
 * back if that bites. Edits are kept locally and posted quietly, so a drag does not wait on a reload.
 */
export function useSections(setting: 'pane' | 'side', names: string[], saved: Layout | undefined, panel: string) {
  const [mine, setMine] = useState<Layout | null>(null)
  const [dragged, setDragged] = useState<string | null>(null)
  // where the dragged section would land: above or below this one, drawn as a line
  const [at, setAt] = useState<{ k: string; after: boolean } | null>(null)
  const lay = mine || saved || {}
  const put = (next: Layout) => {
    setMine(next)
    void post('/api/settings', { [setting]: next })
  }
  // a section the saved order does not name yet (a new one) goes last, in `names` order
  const order = [...(lay.order || []).filter((k) => names.includes(k)), ...names.filter((n) => !lay.order?.includes(n))]
  const move = (from: string, to: string, after: boolean) => {
    if (from === to) return
    const rest = order.filter((k) => k !== from)
    rest.splice(rest.indexOf(to) + (after ? 1 : 0), 0, from)
    put({ ...lay, order: rest })
  }
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
          const outside = r && (e.clientX || e.clientY) && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
          if (outside && !out) put({ ...lay, out: [...(lay.out || []), k] })
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

/** A section popped out into its own window; ⤓ puts it back. */
export function Out({ id, label, onDock, children }: { id: string; label: string; onDock: () => void; children: ReactNode }) {
  const { box, el, drag, grip, style } = useFloatBox(
    `dashy-sec-${id}`,
    () => ({ x: Math.max(8, Math.round(window.innerWidth / 2 - 210)), y: 90, w: 420, h: 360, max: false }),
    '.ib',
  )
  return (
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
    </div>
  )
}
