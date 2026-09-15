import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useNow } from '../usePoll'
import { moved, type Got, type Pr } from '../stories'

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

/** "updated 4 min ago", ticking on its own so the pop-up around it does not re-render. */
function Updated({ at }: { at: number }) {
  const mins = Math.floor((useNow(30_000) / 1000 - at) / 60)
  const ago = mins < 1 ? 'just now' : mins < 60 ? `${mins} min ago` : `${Math.floor(mins / 60)} h ago`
  return <div className="updated">updated {ago}</div>
}

/** One followed user as a footer pill: click for what they have been on for the last day, × to unfollow. */
export function Story({ login, every, onUnfollow }: { login: string; every: number; onUnfollow: () => void }) {
  const [got, setGot] = useState<Got | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  /** The PRs a poll found new or moved since the story on the pill; they pop up on their own. */
  const [news, setNews] = useState<Pr[]>([])
  const last = useRef<Got | null>(null)
  /** A check still out: the next poll tick skips rather than stacking a second one on it. */
  const inflight = useRef(false)
  const el = useRef<HTMLDivElement>(null)
  /** Where the pop-up sits: fixed above the pill, so a dock that scrolls sideways does not clip it. */
  const [left, setLeft] = useState(0)
  const place = () => {
    const r = el.current?.getBoundingClientRect()
    if (r) setLeft(Math.max(8, Math.min(r.left, window.innerWidth - 368)))
  }

  /** `quiet` is the poll: no skeleton, and a failed check keeps the story already there. */
  const load = (fresh: boolean, quiet = false) => {
    if (!quiet) {
      setBusy(true)
      setErr('')
    }
    inflight.current = true
    api(`/api/story?login=${encodeURIComponent(login)}${fresh ? '&fresh=1' : ''}`)
      .then(async (r) => {
        if (r.ok) {
          const next: Got = await r.json()
          if (last.current && next.at !== last.current.at) {
            const moves = moved(last.current, next)
            // a PR only ageing out of the window rewrites the story but is not new work
            if (moves.length) {
              place()
              setNews(moves)
            }
          }
          last.current = next
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
  useEffect(() => load(false), [login])
  // ponytail: polls on the board's own interval. The server searches every time but only asks the model
  // again when this user's PRs moved, so a quiet week costs one search per tick.
  useEffect(() => {
    if (!every) return
    // like the board's own poll: nothing while the window is hidden
    const id = setInterval(() => document.hidden || inflight.current || load(false, true), every * 1000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [login, every])
  // news that came in while the story was open is already on screen: closing drops it
  const close = () => {
    setNews([])
    setOpen(false)
  }
  const toggle = () => {
    place()
    setNews([])
    setOpen((o) => !o)
  }
  // a click anywhere else, or Esc, puts the pop-up away.
  // ponytail: Esc captures and stops, like ActsMenu, since the board reads Escape as "open the menu"
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => el.current?.contains(e.target as Node) || close()
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      close()
    }
    document.addEventListener('mousedown', away)
    window.addEventListener('keydown', esc, true)
    return () => {
      document.removeEventListener('mousedown', away)
      window.removeEventListener('keydown', esc, true)
    }
  }, [open])

  return (
    <div ref={el} className={`chip${news.length ? ' news' : ''}${open ? ' on' : ''}`}>
      <button onClick={toggle} title={`what ${login} is working on`}>
        {news.length ? <i /> : null}
        {login}
      </button>
      <button className="x" onClick={onUnfollow} title={`unfollow ${login}`} aria-label={`unfollow ${login}`}>
        ×
      </button>
      {open ? (
        <div className="pop" style={{ left }} role="dialog" aria-label={`${login}'s story`}>
          <div className="poph">
            <b>{login}</b>
            <div style={{ flex: 1 }} />
            <button className="iconbtn" onClick={() => load(true)} disabled={busy} title="ask again">
              ⟳
            </button>
            <button className="iconbtn" onClick={close} title="close">
              ✕
            </button>
          </div>
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
                <div className="krow" key={p.url}>
                  <s>
                    {p.repo}#{p.number} {p.title}
                  </s>
                </div>
              ))}
            </details>
          ) : null}
        </div>
      ) : news.length ? (
        <div className="pop" style={{ left }} role="status">
          <div className="poph">
            <b>{login}</b>
            <span>new work</span>
            <div style={{ flex: 1 }} />
            <button className="iconbtn" onClick={() => setNews([])} title="dismiss">
              ✕
            </button>
          </div>
          <ul className="go" onClick={toggle}>
            {news.map((p) => (
              <li key={p.url}>
                {p.repo}#{p.number} {p.title}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
