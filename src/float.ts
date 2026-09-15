import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

export type Box = { x: number; y: number; w: number; h: number; max: boolean }

/** Kept on screen: never wider than the window, and the header always reachable. */
export function fit(b: Box): Box {
  const w = Math.min(b.w, window.innerWidth)
  const h = Math.min(b.h, window.innerHeight)
  return { ...b, w, h, x: Math.min(Math.max(0, b.x), window.innerWidth - w), y: Math.min(Math.max(0, b.y), window.innerHeight - h) }
}

function stored(key: string, make: () => Box): Box {
  try {
    const b = JSON.parse(localStorage.getItem(key) || 'null') as Box | null
    if (b) return fit(b)
  } catch {
    /* storage unavailable */
  }
  return make()
}

/** A draggable, resizable window that remembers where you put it.
 *
 * ponytail: the code viewer grew this first and the shortcut sheet wanted the same thing, so it
 * lives here rather than twice. The corner handle is the browser's own `resize: both`; the observer
 * only reads back what it did, which is why there is no resize pointer code.
 *
 * `noDrag` is a selector for the controls in the header bar — without it, clicking a tab in the
 * header starts a drag instead.
 */
export function useFloatBox(key: string, make: () => Box, noDrag: string) {
  const [box, setBox] = useState(() => stored(key, make))
  const el = useRef<HTMLDivElement>(null)
  const grab = useRef<{ dx: number; dy: number } | null>(null)
  const sizing = useRef<{ x: number; y: number; w: number; h: number } | null>(null)

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(box))
    } catch {
      /* storage unavailable */
    }
  }, [key, box])

  useEffect(() => {
    const node = el.current
    if (!node) return
    const ro = new ResizeObserver(() => {
      setBox((b) => {
        // hidden (a minimized card) measures 0 by 0, which is not a size to remember
        if (!node.offsetWidth) return b
        return b.max || (node.offsetWidth === b.w && node.offsetHeight === b.h) ? b : { ...b, w: node.offsetWidth, h: node.offsetHeight }
      })
    })
    ro.observe(node)
    const onResize = () => setBox(fit)
    window.addEventListener('resize', onResize)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', onResize)
    }
  }, [])

  const drag = {
    onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => {
      if (box.max || (e.target as HTMLElement).closest(noDrag)) return
      grab.current = { dx: e.clientX - box.x, dy: e.clientY - box.y }
      e.currentTarget.setPointerCapture(e.pointerId)
    },
    onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => {
      const g = grab.current
      if (g) setBox((b) => fit({ ...b, x: e.clientX - g.dx, y: e.clientY - g.dy }))
    },
    onPointerUp: () => (grab.current = null),
    onLostPointerCapture: () => (grab.current = null),
    onDoubleClick: (e: React.MouseEvent<HTMLDivElement>) => {
      if (!(e.target as HTMLElement).closest(noDrag)) setBox((b) => ({ ...b, max: !b.max }))
    },
  }

  /** A corner handle of our own; its floors are `.fw`'s min-width and min-height. ponytail: `resize: both` alone does not answer in the Linux app's webview
   *  when a scrolling child covers the corner, so a box that needs resizing there spreads these on a div. */
  const grip = {
    onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => {
      if (box.max) return
      e.stopPropagation()
      sizing.current = { x: e.clientX, y: e.clientY, w: box.w, h: box.h }
      e.currentTarget.setPointerCapture(e.pointerId)
    },
    onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => {
      const s = sizing.current
      if (s) setBox((b) => fit({ ...b, w: Math.max(280, s.w + e.clientX - s.x), h: Math.max(200, s.h + e.clientY - s.y) }))
    },
    onPointerUp: () => (sizing.current = null),
    onLostPointerCapture: () => (sizing.current = null),
  }

  const style = box.max ? undefined : { left: box.x, top: box.y, width: box.w, height: box.h }
  return { box, el, drag, grip, style }
}
