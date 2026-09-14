import type { Row, StateData } from '../types'
import { useNow } from '../usePoll'
import type { VisSection } from '../board'
import { ALL, buckets, inBucket, settings } from '../board'
import { age, avatar, PALETTE, rowState, SECTION_EMPTY, SECTION_HINT, SECTION_TONE, SPINNER, tone } from '../tokens'

type Props = {
  data: StateData | null
  secs: VisSection[]
  sel: string
  read: Record<string, string>
  onReadAll: () => void
  query: string
  onQuery: (v: string) => void
  failing: boolean
  onFailing: () => void
  drafts: boolean
  onDrafts: () => void
  bucket: string[]
  onBucket: (name: string) => void
  expanded: Record<string, boolean>
  onExpand: (url: string) => void
  onSelect: (uid: string) => void
  onOpen: () => void
  onMenu: (row: Row, at: { x: number; y: number }) => void
}

function mineNote(prs: Row[]): string {
  const work = prs.filter((p) => (p.status || '').startsWith('✗')).length
  const wait = prs.filter((p) => /^[·↻]/.test(p.status || '')).length
  return [work ? `${work} need work` : '', wait ? `${wait} waiting` : ''].filter(Boolean).join(' · ')
}

function PrRow({ p, child, sel, unread, expanded, onExpand, onSelect, onOpen, onMenu }: {
  p: Row
  child?: boolean
  sel: string
  unread: boolean
  expanded: Record<string, boolean>
  onExpand: (url: string) => void
  onSelect: (uid: string) => void
  onOpen: () => void
  onMenu: (row: Row, at: { x: number; y: number }) => void
}) {
  const st = rowState(p)
  const pal = PALETTE[st.key] || PALETTE.idle
  const ci = tone(p.checks)
  const revs = (p.reviewers || '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 3)
    .map((r) => {
      const rp = PALETTE[tone(r[0])] || PALETTE.idle
      return (
        <div key={r} className="rev" style={{ background: rp.bg, color: rp.fg }}>
          {r.slice(1)}
        </div>
      )
    })
  const end = p.busy ? (
    <>
      <i className="spinner" />
      <span className="mono" style={{ fontSize: 11, color: 'var(--cyan)' }}>
        {st.label}
      </span>
    </>
  ) : (
    <>
      {ci ? <div className="ci" style={{ background: PALETTE[ci].fg }} title={`checks ${p.checks}`} /> : null}
      <div className="state" style={{ background: pal.bg, border: `1px solid ${pal.border}`, color: pal.fg }}>
        {st.label.slice(0, 24)}
      </div>
    </>
  )
  const n = p.older?.length || 0
  const twist = n ? (
    <div
      className="twist"
      title={`${n + 1} reviews of this PR — space, or click`}
      onClick={(e) => {
        e.stopPropagation()
        onExpand(p.url)
      }}
    >
      {expanded[p.url] ? '▾' : '▸'}
      <em>{n + 1}</em>
    </div>
  ) : (
    <div className="twist" />
  )
  return (
    <div
      className={`pr${p.uid === sel ? ' sel' : ''}${child ? ' child' : ''}${unread ? ' unread' : ''}`}
      onClick={() => onSelect(p.uid)}
      onDoubleClick={() => onOpen()}
      onContextMenu={(e) => {
        e.preventDefault()
        onSelect(p.uid)
        onMenu(p, { x: e.clientX, y: e.clientY })
      }}
    >
      {child ? <div className="twist" /> : twist}
      <div className="age">{age(p.updatedAt)}</div>
      <div className="repo">{(p.repo || '').split('/').pop()}</div>
      <div className="num">#{p.number}</div>
      <div className="txt">
        <b>
          {child ? '└ ' : ''}
          {p.isDraft ? <span style={{ color: 'var(--amber)' }}>draft</span> : null}
          {p.isDraft ? ' ' : ''}
          {p.title}
        </b>
      </div>
      <div className="who">
        <div className="av" style={{ background: avatar(p.author || '?') }}>
          {(p.author || '?')[0].toUpperCase()}
        </div>
        <span>{p.author}</span>
      </div>
      <div className="revs">{revs}</div>
      <div className="end">{end}</div>
    </div>
  )
}

/** The queue: filter bar, then the sections, their PRs, and the folded REVIEWED runs. */
export function Queue(p: Props) {
  const d = p.data
  // running: not read here, but a running row's elapsed label (rowState) only moves when this re-renders
  const now = useNow(d?.running || !d?.fetchedAt ? 1000 : 0)
  const shownSecs = inBucket(p.secs, p.bucket)
  const shown = shownSecs.flatMap((s) => s.prs)
  const failing = shown.filter((x) => tone(x.checks) === 'changes').length
  // ponytail: counted over the BUCKET, not the whole board. The chip sits beside the tabs and filters
  // what they show, so a count of rows you are not looking at is a number that cannot be acted on.
  const drafts = shown.filter((x) => x.isDraft).length

  // by url: a PR still open elsewhere also has a REVIEWED row, and counting both showed 8 for 7
  const unread = new Set(shown.filter((x) => p.read[x.url] !== x.updatedAt).map((x) => x.url)).size

  if (d && !d.fetchedAt && !d.sections?.length) {
    return (
      <div className="list scroll">
        <div className="splash">
          <div className="logo">
            <img src="/head.png" alt="" />
          </div>
          <div style={{ color: 'var(--amber)', fontWeight: 600 }}>
            {SPINNER[Math.floor(now / 125) % SPINNER.length]} fetching pull requests…
          </div>
          <div className="mono" style={{ fontSize: 11 }}>
            created by
          </div>
          <div style={{ color: 'var(--cyan)', fontWeight: 600, letterSpacing: '.2em' }}>M a r t i n   S o r i a   R ø v a n g</div>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="bar">
        {/* ponytail: the tabs ARE the section headers now. One queue at a time, so the thing that used
            to be a fold caret is the thing that picks what you are looking at. */}
        <kbd className="hint" title="previous / next queue">[ ]</kbd>
        <div className="tabs scroll" role="tablist">
          {buckets(p.secs).map((b) => (
            <button
              key={b.key}
              className="tab"
              role="tab"
              aria-selected={p.bucket.includes(b.key) || (b.key === ALL && !p.bucket.length)}
              onClick={() => p.onBucket(b.key)}
            >
              {b.label}
              <span className="n">{b.n}</span>
            </button>
          ))}
        </div>
        <span className="vr" />
        <div className="fgroup">
          <span className="fi" aria-hidden="true">
            ≡
          </span>
          <button className="chip" aria-pressed={p.failing} disabled={!failing && !p.failing && !p.drafts} onClick={p.onFailing}>
            CI failing <b>{failing}</b>
          </button>
          <button className="chip" aria-pressed={p.drafts} disabled={!drafts && !p.drafts && !p.failing} onClick={p.onDrafts}>
            Drafts <b>{drafts}</b>
          </button>
          {unread ? (
            <button className="chip" onClick={p.onReadAll}>
              Read all <b>{unread}</b>
            </button>
          ) : null}
          {p.failing || p.drafts ? (
            <button
              className="chip clr"
              onClick={() => {
                if (p.failing) p.onFailing()
                if (p.drafts) p.onDrafts()
              }}
            >
              Clear
            </button>
          ) : null}
        </div>
        <div className="grow" />
        <label className="search">
          <kbd className="hint">/</kbd>
          <input id="q" value={p.query} placeholder="filter by title, repo, author" onChange={(e) => p.onQuery(e.target.value)} />
        </label>
        <div className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
          updated{' '}
          {d?.fetchedAt
            ? ((a: string) => (a === 'now' ? 'just now' : a + ' ago'))(age(new Date(d.fetchedAt * 1000).toISOString()))
            : 'never'}
        </div>
      </div>
      <div className="list scroll">
        {shownSecs.map((s) => {
          const labels = new Set(s.prs.map((x) => x.team || ''))
          const rows =
            labels.size > 1
              ? [...s.prs].sort(
                  (a, b) => Number(a.team === '') - Number(b.team === '') || (a.team || '').localeCompare(b.team || ''),
                )
              : s.prs
          let seen: string | null = null
          return (
            <div key={s.name}>
              {/* ponytail: the section name appears whenever more than one queue is on screen,
                  because there it is the only thing saying which one a row came from. With a single
                  tab picked, the tab says it. */}
              {shownSecs.length > 1 ? (
                <div className="grp">
                  <span style={{ color: SECTION_TONE[s.name] || 'var(--dim)' }}>{s.name}</span>
                  <span className="hint">
                    {s.name === 'MINE' ? mineNote(s.prs) || SECTION_HINT[s.name] : SECTION_HINT[s.name] || ''}
                  </span>
                  <hr />
                </div>
              ) : null}
              {s.error ? (
                <div className="none err">{s.error.split('\n')[0]}</div>
              ) : !s.prs.length ? (
                <div className="empty">
                  <b>Clear</b>
                  {s.name === 'REVIEWED' && settings(d).window
                    ? `Nothing reviewed in the last ${settings(d).window}h.`
                    : SECTION_EMPTY[s.name] || 'Nothing here.'}
                </div>
              ) : (
                rows.map((row) => {
                  const label = row.team || ''
                  const sep = labels.size > 1 && label !== seen ? ((seen = label), true) : false
                  return (
                    <div key={row.uid}>
                      {sep ? (
                        <div className="grp">
                          <span>{label || 'not bound to a team'}</span>
                          <hr />
                        </div>
                      ) : null}
                      <PrRow
                        p={row}
                        sel={p.sel}
                        unread={p.read[row.url] !== row.updatedAt}
                        expanded={p.expanded}
                        onExpand={p.onExpand}
                        onSelect={p.onSelect}
                        onOpen={p.onOpen}
                        onMenu={p.onMenu}
                      />
                      {p.expanded[row.url]
                        ? row.older.map((o) => (
                            <PrRow
                              key={o.uid}
                              p={o}
                              child
                              sel={p.sel}
                              unread={false}
                              expanded={p.expanded}
                              onExpand={p.onExpand}
                              onSelect={p.onSelect}
                              onOpen={p.onOpen}
                              onMenu={p.onMenu}
                            />
                          ))
                        : null}
                    </div>
                  )
                })
              )}
            </div>
          )
        })}
        {p.secs.length ? null : <div className="none">nothing fetched yet</div>}
      </div>
    </>
  )
}
