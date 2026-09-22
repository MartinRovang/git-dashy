import type { Row, StateData } from '../types'
import { useNow } from '../usePoll'
import type { Only, VisSection } from '../board'
import { FOLDABLE, buckets, chips, emptyLine, folded, inBucket, isRead, onScreen } from '../board'
import { Glyph } from './Glyph'
import { age, avatar, PALETTE, rowState, SECTION_HINT, SECTION_TONE, SPINNER, tone } from '../tokens'

type Props = {
  data: StateData | null
  secs: VisSection[]
  sel: string
  read: Record<string, string>
  onReadAll: () => void
  query: string
  only: Only
  onQuery: (v: string) => void
  failing: boolean
  onFailing: () => void
  drafts: boolean
  onDrafts: () => void
  hiddenN: number
  showHidden: boolean
  onHidden: () => void
  bucket: string[]
  onBucket: (name: string) => void
  expanded: Record<string, boolean>
  onExpand: (url: string) => void
  unfolded: Record<string, boolean>
  onFold: (name: string) => void
  onSelect: (uid: string) => void
  onOpen: () => void
  onMenu: (row: Row, at: { x: number; y: number }) => void
}

function mineNote(prs: Row[]): string {
  const work = prs.filter((p) => (p.status || '').startsWith('✗')).length
  const wait = prs.filter((p) => /^[·↻]/.test(p.status || '')).length
  return [work ? `${work} need work` : '', wait ? `${wait} waiting` : ''].filter(Boolean).join(' · ')
}

function PrRow({ p, me, child, sel, unread, expanded, onExpand, onSelect, onOpen, onMenu }: {
  p: Row
  /** Your login: your own PRs' author is dimmed, so others' stand out. */
  me?: string
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
      title={`${n + 1} reviews of this PR (space, or click)`}
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
          {p.db ? (
            <span style={{ color: 'var(--amber)' }} title="changes the database: see DATABASE in the pane" aria-label="changes the database">
              ⚠{' '}
            </span>
          ) : null}
          {/* before the title: after it, a long title's ellipsis clipped the pill away */}
          {p.scores.map((s) => (
            <span key={s.name} className={`score g${s.grade}`} title={`${s.name} ${s.score}/100: ${s.note}`}>
              <Glyph kind="passive" name={s.name} size={13} />
              {s.grade} {s.score}
            </span>
          ))}
          {p.title}
        </b>
      </div>
      <div className={`who${me && p.author?.toLowerCase() === me.toLowerCase() ? ' mine' : ''}`}>
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
  const shown = onScreen(p.secs, p.bucket, p.unfolded)

  // by url: a PR still open elsewhere also has a REVIEWED row, and counting both showed 8 for 7
  const unread = new Set(shown.filter((x) => !isRead(p.read, x)).map((x) => x.url)).size

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
              aria-selected={p.bucket.includes(b.key)}
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
          {chips(p.secs, p.bucket, p.failing, p.drafts).map((c) => (
            <button
              key={c.key}
              className="chip"
              aria-pressed={c.on}
              disabled={c.off}
              onClick={c.key === 'failing' ? p.onFailing : p.onDrafts}
            >
              {c.label} <b>{c.n}</b>
            </button>
          ))}
          {p.hiddenN || p.showHidden ? (
            <button className="chip" aria-pressed={p.showHidden} onClick={p.onHidden}>
              Hidden <b>{p.hiddenN}</b>
            </button>
          ) : null}
          {unread ? (
            <button className="chip" onClick={p.onReadAll}>
              Read all <b>{unread}</b>
            </button>
          ) : null}
        </div>
        <div className="grow" />
        <label className="search">
          <kbd className="hint">/</kbd>
          <input id="q" value={p.query} placeholder="filter by title, repo, author" onChange={(e) => p.onQuery(e.target.value)} />
        </label>
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
          const named = shownSecs.length > 1
          const shut = folded(s.name, shownSecs.length, p.unfolded)
          const foldable = named && FOLDABLE.includes(s.name)
          const hint = s.name === 'MINE' ? mineNote(s.prs) || SECTION_HINT[s.name] : SECTION_HINT[s.name] || ''
          return (
            <div key={s.name}>
              {/* ponytail: only the section NAME is redundant under a single tab — the tab says it.
                  The hint beside it is not: "3 need work · 2 waiting" is the one thing that row
                  carried that no tab does, and it vanished in the queue you opened to read it. */}
              {named || hint ? (
                <div
                  className="grp"
                  onClick={foldable ? () => p.onFold(s.name) : undefined}
                  style={foldable ? { cursor: 'pointer' } : undefined}
                >
                  {named ? (
                    <span style={{ color: SECTION_TONE[s.name] || 'var(--dim)' }}>
                      {foldable ? (shut ? '▸ ' : '▾ ') : ''}
                      {s.name}
                      {shut ? ` · ${s.prs.length}` : ''}
                    </span>
                  ) : null}
                  <span className="hint">{hint}</span>
                  <hr />
                </div>
              ) : null}
              {shut ? null : s.error ? (
                <div className="none err">{s.error.split('\n')[0]}</div>
              ) : !s.prs.length ? (
                <div className="empty">{emptyLine(d, s.name, p.query, p.failing, p.drafts, p.only)}</div>
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
                        me={d?.me}
                        sel={p.sel}
                        unread={!isRead(p.read, row)}
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
                              me={d?.me}
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
        {p.secs.length ? null : d ? (
          <div className="none">nothing fetched yet</div>
        ) : (
          <div className="skel" aria-busy="true" style={{ padding: '14px 18px' }}>
            {[70, 55, 80, 60, 75, 50].map((w, i) => (
              <i key={i} style={{ width: `${w}%`, height: 22 }} />
            ))}
          </div>
        )}
      </div>
    </>
  )
}
