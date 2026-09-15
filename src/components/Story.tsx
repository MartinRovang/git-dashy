import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, errorText, post } from '../api'
import { useFloatBox } from '../float'
import { useNow } from '../usePoll'
import type { Followed } from '../stories'

/** The model's "- " lines as a list; anything that is not a list (an older cached story) as a paragraph. */
function Summary({ text }: { text: string }) {
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean)
  const bullets = lines.filter((l) => /^[-*•]\s/.test(l)).map((l) => l.replace(/^[-*•]\s+/, ''))
  if (!bullets.length) return <p>{text}</p>
  return (
    <ul>
      {bullets.map((b, i) => (
        <li key={i}>{b}</li>
      ))}
    </ul>
  )
}

/** "updated 4 min ago", ticking on its own so the card around it does not re-render. */
function Updated({ at }: { at: number }) {
  const mins = Math.floor((useNow(30_000) / 1000 - at) / 60)
  const ago = mins < 1 ? 'just now' : mins < 60 ? `${mins} min ago` : `${Math.floor(mins / 60)} h ago`
  return <div className="updated">updated {ago}</div>
}

type Got = {
  at: number
  summary: string
  prs: { repo: string; number: number; title: string }[]
  /** One sentence, only when the model judged this a different problem from the last story. */
  shift?: string
}

/** One followed user's floating card: what they have been on for the last 3 days, in the model's words. */
export function Story({ f, i, every, dock, onPatch, onClose }: { f: Followed; i: number; every: number; dock: HTMLElement | null; onPatch: (part: Partial<Followed>) => void; onClose: () => void }) {
  const { box, el, drag, grip, style } = useFloatBox(
    `story:${f.login}`,
    () => ({ x: window.innerWidth - 360 - i * 28, y: 70 + i * 28, w: 340, h: 230, max: false }),
    '.iconbtn',
  )
  const [got, setGot] = useState<Got | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  /** A check still out: the next poll tick skips rather than stacking a second one on it. */
  const inflight = useRef(false)

  /** `quiet` is the poll: no skeleton, and a failed check keeps the story already on the card. */
  const load = (fresh: boolean, quiet = false) => {
    if (!quiet) {
      setBusy(true)
      setErr('')
    }
    inflight.current = true
    api(`/api/story?login=${encodeURIComponent(f.login)}${fresh ? '&fresh=1' : ''}`)
      .then(async (r) => {
        if (r.ok) {
          const next: Got = await r.json()
          setGot(next)
          setErr('')
        } else if (!quiet) setErr(await errorText(r))
      })
      .catch(() => quiet || setErr('server gone'))
      .finally(() => {
        inflight.current = false
        if (!quiet) setBusy(false)
      })
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(false), [f.login])
  // ponytail: polls on the board's own interval. The server searches every time but only asks the model
  // again when this user's PRs moved, so a quiet week costs one search per tick.
  useEffect(() => {
    if (!every) return
    // like the board's own poll: nothing while the window is hidden
    const id = setInterval(() => document.hidden || inflight.current || load(false, true), every * 1000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.login, every])

  /** What the model called a change of direction, until it has been read. */
  const shift = got?.shift || ''
  /** Reading it is what clears it -- on the server too, or the next poll brings the same mark back. */
  const read = () => {
    if (!shift) return
    setGot((g) => (g ? { ...g, shift: '' } : g))
    void post('/api/story/seen', { login: f.login })
  }
  const restore = () => {
    read()
    onPatch({ min: false })
  }
  const chip = f.min && dock
    ? createPortal(
        <div className={`chip${shift ? ' news' : ''}`}>
          <button onClick={restore} title="restore the card">
            {shift ? <i /> : null}
            {f.login}
          </button>
          {shift && got ? (
            <div className="pop" role="status">
              <div className="poph">
                <b>{f.login}</b>
                <span>new direction</span>
                <div style={{ flex: 1 }} />
                <button className="iconbtn" onClick={read} title="dismiss">
                  ✕
                </button>
              </div>
              <div onClick={restore} style={{ cursor: 'pointer' }}>
                <p className="shiftline">{shift}</p>
                <Summary text={got.summary} />
              </div>
            </div>
          ) : null}
        </div>,
        dock,
      )
    : null

  return (
    <>
      {chip}
      <div ref={el} hidden={!!f.min} className={`fw story${box.max ? ' max' : ''}`} style={style} role="dialog" aria-label={`${f.login}'s story`}>
        <div className="bar" title="drag to move, double-click to maximize" {...drag}>
          <b>{f.login}</b>
          <div style={{ flex: 1 }} />
          <button className="iconbtn" onClick={() => onPatch({ min: true })} title="minimize to the footer">
            –
          </button>
          <button className="iconbtn" onClick={() => load(true)} disabled={busy} title="ask again">
            ⟳
          </button>
          <button className="iconbtn" onClick={onClose} title="unfollow">
            ✕
          </button>
        </div>
        <div className="keys scroll">
          {shift ? (
            <div className="shift" role="status">
              <b>new direction</b>
              <span>{shift}</span>
              <button className="iconbtn" onClick={read} title="dismiss">
                ✕
              </button>
            </div>
          ) : null}
          {/* above the story, not instead of it: a failed ⟳ leaves the last good one readable */}
          {err ? <p style={{ color: 'var(--red)' }}>✗ {err}</p> : null}
          {busy ? (
            <div className="skel" aria-busy="true">
              <span className="shimtext">fetching summaries…</span>
              <i style={{ width: '92%' }} />
              <i style={{ width: '78%' }} />
              <i style={{ width: '85%' }} />
            </div>
          ) : got ? (
            <>
              <Summary text={got.summary} />
              <Updated at={got.at} />
            </>
          ) : null}
          {!busy && got?.prs.length ? (
            <details>
              <summary>
                {got.prs.length} PR{got.prs.length === 1 ? '' : 's'}
              </summary>
              {got.prs.map((p) => (
                <div className="krow" key={`${p.repo}#${p.number}`}>
                  <s>
                    {p.repo}#{p.number} {p.title}
                  </s>
                </div>
              ))}
            </details>
          ) : null}
        </div>
        {box.max ? null : <div className="fgrip" title="drag to resize" {...grip} />}
      </div>
    </>
  )
}
