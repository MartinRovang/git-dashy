import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { groups } from '../board'
import type { Code, CodeRow, Row } from '../types'
import { FINDING_TONE, MARK } from '../tokens'

function CodeRowView({ r }: { r: CodeRow }) {
  if (r.kind === 'hunk') return <div className="hunk">{r.header}</div>
  if (r.kind === 'line')
    return (
      <div className={`ln ${r.mark ? 'marked' : r.sign === '+' ? 'add' : r.sign === '-' ? 'del' : ''}`}>
        <span className="m" style={{ color: FINDING_TONE[r.mark] || 'transparent' }}>
          {r.mark ? MARK[r.mark] : ' '}
        </span>
        <span className="n">{r.del ? '' : r.n}</span>
        <span>{r.sign}</span>
        <span className="t">{r.text.replace(/\t/g, '    ')}</span>
      </div>
    )
  if (r.kind === 'note')
    return (
      <div className="cnote">
        <b style={{ color: FINDING_TONE[r.mark] || 'var(--dim)' }}>{r.mark.toUpperCase()}</b>
        <span>{r.text}</span>
      </div>
    )
  if (r.kind === 'orphan')
    return (
      <div className="orphan">
        <b style={{ color: FINDING_TONE[r.mark] || 'var(--dim)' }}>{r.mark.toUpperCase()}</b> {r.text} <em>· {r.loc} {r.why}</em>
      </div>
    )
  return <div style={{ height: 8 }} />
}

type Box = { x: number; y: number; w: number; h: number; max: boolean }
const KEY = 'dashy-code-box'

function initialBox(): Box {
  const W = window.innerWidth
  const H = window.innerHeight
  try {
    const b = JSON.parse(localStorage.getItem(KEY) || 'null') as Box | null
    if (b) return fit(b)
  } catch {
    /* storage unavailable */
  }
  return { x: Math.round(W * 0.15), y: Math.round(H * 0.12), w: Math.round(W * 0.7), h: Math.round(H * 0.76), max: false }
}

/** Kept on screen: never wider than the window, and the header always reachable. */
function fit(b: Box): Box {
  const w = Math.min(b.w, window.innerWidth)
  const h = Math.min(b.h, window.innerHeight)
  return { ...b, w, h, x: Math.min(Math.max(0, b.x), window.innerWidth - w), y: Math.min(Math.max(0, b.y), window.innerHeight - h) }
}

/** A floating diff window over the board: files down the left, the picked file's diff on the right. */
export function CodeViewer({
  p,
  c,
  scope,
  context,
  at,
  onScope,
  onContext,
  onAt,
  focused,
  onClose,
}: {
  p: Row
  c: Code | null
  scope: string
  context: number
  at: number
  onScope: (v: string) => void
  onContext: () => void
  onAt: (i: number) => void
  focused: boolean
  onClose: () => void
}) {
  const [box, setBox] = useState(initialBox)
  const el = useRef<HTMLDivElement>(null)
  const grab = useRef<{ dx: number; dy: number } | null>(null)
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(box))
    } catch {
      /* storage unavailable */
    }
  }, [box])
  // ponytail: the corner handle is the browser's own (resize: both); this only reads back what it did
  useEffect(() => {
    const ro = new ResizeObserver(() => {
      const r = el.current
      if (!r || box.max) return
      if (r.offsetWidth !== box.w || r.offsetHeight !== box.h) setBox((b) => ({ ...b, w: r.offsetWidth, h: r.offsetHeight }))
    })
    ro.observe(el.current!)
    const onResize = () => setBox(fit)
    window.addEventListener('resize', onResize)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', onResize)
    }
  }, [box.max, box.w, box.h])
  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (box.max || (e.target as HTMLElement).closest('.tab, .ib')) return
    grab.current = { dx: e.clientX - box.x, dy: e.clientY - box.y }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = grab.current
    if (g) setBox((b) => fit({ ...b, x: e.clientX - g.dx, y: e.clientY - g.dy }))
  }
  const scoped = scope === 'marks'
  const gs = c && !c.pending ? groups(c.rows) : []
  const cur = gs.length ? Math.max(0, Math.min(at, gs.length - 1)) : 0
  const g = gs[cur]
  return (
    <div
      ref={el}
      className={`cv${box.max ? ' max' : ''}${focused ? ' focus' : ''}`}
      style={box.max ? undefined : { left: box.x, top: box.y, width: box.w, height: box.h }}
    >
      <div
        className="bar"
        title="drag to move, double-click to maximize"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={() => (grab.current = null)}
        onLostPointerCapture={() => (grab.current = null)}
        onDoubleClick={(e) => {
          if (!(e.target as HTMLElement).closest('.tab, .ib')) setBox((b) => ({ ...b, max: !b.max }))
        }}
      >
        <span style={{ color: 'var(--pink)' }} className="mono">#{p.number}</span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--dim)' }}>{p.repo}</span>
        <span className="cvtitle">{p.title}</span>
        <div style={{ flex: 1 }} />
        <span className="lab">SCOPE</span>
        <span className={`tab${scoped ? ' on' : ''}`} onClick={() => onScope('marks')}>
          <kbd className="hint">D</kbd>marks only
        </span>
        <span className={`tab${scoped ? '' : ' on'}`} onClick={() => onScope('diff')}>
          full diff
        </span>
        {scoped ? (
          <span className="tab" onClick={onContext}>
            <kbd className="hint">c</kbd>context ±{context}
          </span>
        ) : null}
        <span className="ib" title="close (esc)" onClick={onClose}>
          ×
        </span>
      </div>
      {!c || c.pending ? (
        <div className="prose cvmsg"><span className="spinner" /> reading the diff…</div>
      ) : c.empty ? (
        <div className="prose cvmsg">{c.empty}</div>
      ) : (
        <div className="cvbody">
          <div className="cvfiles scroll">
            {gs.map((x, i) => (
              <div key={x.label} className={`cvfile${i === cur ? ' on' : ''}`} onClick={() => onAt(i)} title={x.label}>
                <span className="path">
                  {x.label.includes('/') ? <em>{x.label.slice(0, x.label.lastIndexOf('/') + 1)}</em> : null}
                  {x.label.split('/').pop()}
                </span>
                <span className="stat">
                  {x.marks ? <b>{x.marks}●</b> : null}
                  {x.add != null ? (
                    <>
                      <i className="a">+{x.add}</i>
                      <i className="d">−{x.dele}</i>
                    </>
                  ) : null}
                </span>
              </div>
            ))}
          </div>
          {/* keyed by file so a new pick starts at the top */}
          <div className="cvdiff code scroll" key={g?.label}>
            {g ? (
              <>
                <div className="file">
                  {g.label}
                  {g.add != null ? <em>+{g.add} −{g.dele}</em> : null}
                </div>
                {g.rows.map((r, i) => <CodeRowView key={i} r={r} />)}
              </>
            ) : (
              <div className="prose">nothing in this scope</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
