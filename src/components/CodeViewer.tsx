import { memo } from 'react'
import { groups } from '../board'
import { useFloatBox, type Box } from '../float'
import type { Code, CodeRow, Row } from '../types'
import { FINDING_TONE, MARK } from '../tokens'

// ponytail: memo so a drag frame (setBox) skips re-rendering every row of a big file
const CodeRowView = memo(function CodeRowView({ r }: { r: CodeRow }) {
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
})

const KEY = 'dashy-code-box'

function initialBox(): Box {
  const W = window.innerWidth
  const H = window.innerHeight
  return { x: Math.round(W * 0.15), y: Math.round(H * 0.12), w: Math.round(W * 0.7), h: Math.round(H * 0.76), max: false }
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
  const { box, el, drag, style } = useFloatBox(KEY, initialBox, '.tab, .ib')
  const scoped = scope === 'marks'
  const gs = c && !c.pending ? groups(c.rows) : []
  const cur = gs.length ? Math.max(0, Math.min(at, gs.length - 1)) : 0
  const g = gs[cur]
  return (
    <div ref={el} className={`cv${box.max ? ' max' : ''}${focused ? ' focus' : ''}`} style={style}>
      <div className="bar" title="drag to move, double-click to maximize" {...drag}>
        <span style={{ color: 'var(--pink)' }} className="mono">#{p.number}</span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--dim)' }}>{p.repo}</span>
        <span className="cvtitle">{p.title}</span>
        <div style={{ flex: 1 }} />
        <span className="lab">SCOPE</span>
        <span className={`tab${scoped ? ' on' : ''}`} onClick={() => onScope('marks')}>
          <kbd className="hint">D</kbd>marks only
        </span>
        <span className={`tab${scoped ? '' : ' on'}`} onClick={() => onScope('diff')}>
          <kbd className="hint">D</kbd>full diff
        </span>
        {scoped ? (
          <span className="tab" onClick={onContext}>
            <kbd className="hint">c</kbd>context ±{context}
          </span>
        ) : null}
        <span className="ib" title="close (esc)" onClick={onClose}>
          ×
          <kbd className="hint">esc</kbd>
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
