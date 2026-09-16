import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, errorText, post } from '../api'
import { useNow } from '../usePoll'
import { lineOnScreen, moved, type Got, type Pr, shownShift } from '../stories'

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
  /** Every PR head this pill has seen: what a poll's PRs are checked against. */
  const seen = useRef<Map<string, string | undefined> | null>(null)
  /** A check still out: the next poll tick skips rather than stacking a second one on it. */
  const inflight = useRef(false)
  /** When the direction mark was last read. A poll that STARTED before that answers with the shift still
   *  on it -- the server had not been told yet -- so its answer must not raise the mark again. */
  const dismissed = useRef(0)
  const el = useRef<HTMLDivElement>(null)
  const pop = useRef<HTMLDivElement>(null)

  /** `quiet` is the poll: no skeleton, and a failed check keeps the story already there. */
  const load = (fresh: boolean, quiet = false) => {
    if (!quiet) {
      setBusy(true)
      setErr('')
    }
    inflight.current = true
    const began = Date.now()
    api(`/api/story?login=${encodeURIComponent(login)}${fresh ? '&fresh=1' : ''}`)
      .then(async (r) => {
        if (r.ok) {
          const next: Got = await r.json()
          next.shift = shownShift(next.shift, began, dismissed.current)
          // the first story only teaches what is already there
          if (!seen.current) moved((seen.current = new Map()), next)
          else {
            const moves = moved(seen.current, next)
            if (moves.length) setNews(moves)
          }
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
  /** What the model called a change of direction, until it has been read. ponytail: separate from `news`,
   *  which lists the pull requests that actually moved. This is a judgement about them, so it earns its own
   *  mark rather than riding the one that means "something was pushed". */
  const shift = got?.shift || ''
  /** The shift the pop-up opened with, kept until it closes: see lineOnScreen. */
  const [openedWith, setOpenedWith] = useState('')
  const line = lineOnScreen(open, openedWith, shift)
  /** Tell the server the mark has been read. Not the local copy: the line has to stay up while the pop-up
   *  is open, or opening the pill is the one gesture that guarantees you never read it. */
  const markRead = () => {
    if (!shift) return
    dismissed.current = Date.now()
    // the text too: the server clears only the shift that was read, not a newer one written meanwhile
    void post('/api/story/seen', { login, shift })
  }
  const forgetShift = () => setGot((g) => (g ? { ...g, shift: '' } : g))
  /** Closing is what marks everything read: both the list of what moved and the direction line have to
   *  survive the render that opens the pop-up, or opening it is the one gesture that guarantees you miss
   *  them. ponytail: `toggle` used to clear on the way in, so neither ever appeared. */
  const done = () => {
    setNews([])
    forgetShift()
    setOpenedWith('')
  }
  const close = () => {
    // the server is told here, since this is the only path every dismissal goes through
    markRead()
    done()
    setOpen(false)
  }
  const toggle = () => {
    markRead()
    if (open) done()
    else setOpenedWith(shift)
    setOpen((o) => !o)
  }

  // ponytail: `open` alone. A poll used to raise the pop-up by itself the moment any PR moved, so a row
  // of pills threw a paragraph of text over the board on its own schedule. The pill wears a badge saying
  // what changed and waits to be opened.
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const r = el.current?.getBoundingClientRect()
      if (r && pop.current) pop.current.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 368))}px`
    }
    place()
    const dock = el.current?.parentElement
    const grew = new ResizeObserver(place)
    if (dock) grew.observe(dock)
    window.addEventListener('resize', place)
    dock?.addEventListener('scroll', place)
    return () => {
      grew.disconnect()
      window.removeEventListener('resize', place)
      dock?.removeEventListener('scroll', place)
    }
  }, [open])
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
    <div ref={el} className={`chip${news.length ? ' news' : ''}${shift ? ' shift' : ''}${open ? ' on' : ''}`}>
      <button
        onClick={toggle}
        title={[
          `what ${login} is working on`,
          news.length ? `${news.length} pull request${news.length === 1 ? '' : 's'} new or pushed since you looked` : '',
          shift ? `changed direction: ${shift}` : '',
        ]
          .filter(Boolean)
          .join(' · ')}
      >
        {login}
        {/* what changed, small enough to sit in a footer: a count of the PRs that moved, and an arrow
            when the model called it a change of direction. The words are behind the click. */}
        {news.length ? <em className="badge">+{news.length}</em> : null}
        {shift ? <em className="badge dir">↗</em> : null}
      </button>
      <button className="x" onClick={onUnfollow} title={`unfollow ${login}`} aria-label={`unfollow ${login}`}>
        ×
      </button>
      {open ? (
        <div ref={pop} className="pop" role="dialog" aria-label={`${login}'s story`}>
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
          {news.length ? (
            <div className="moved">
              <b>new or pushed</b>
              <ul>
                {news.map((p) => (
                  <li key={p.url}>
                    {p.repo}#{p.number} {p.title}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {/* the mark, above the story it changed into. It survives this render: `read` has already told
              the server, and taking the line away the instant the pop-up opens is the one way to miss it. */}
          {line ? (
            <div className="shiftline" role="status">
              <b>new direction</b>
              <span>{line}</span>
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
                <div className="krow" key={p.url}>
                  <s>
                    {p.repo}#{p.number} {p.title}
                  </s>
                </div>
              ))}
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
