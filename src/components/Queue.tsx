import type { Row, StateData } from '../types'
import type { VisSection } from '../board'
import { settings } from '../board'
import { age, avatar, PALETTE, rowState, SECTION_HINT, SECTION_TONE, SPINNER, tone } from '../tokens'

type Props = {
  data: StateData | null
  secs: VisSection[]
  now: number
  sel: string
  read: Record<string, string>
  query: string
  onQuery: (v: string) => void
  failing: boolean
  onFailing: () => void
  folded: Record<string, boolean>
  expanded: Record<string, boolean>
  onFold: (name: string) => void
  onExpand: (url: string) => void
  onSelect: (uid: string) => void
  onOpen: () => void
}

function mineNote(prs: Row[]): string {
  const work = prs.filter((p) => (p.status || '').startsWith('✗')).length
  const wait = prs.filter((p) => /^[·↻]/.test(p.status || '')).length
  return [work ? `${work} need work` : '', wait ? `${wait} waiting` : ''].filter(Boolean).join(' · ')
}

function PrRow({ p, child, sel, unread, expanded, onExpand, onSelect, onOpen }: {
  p: Row
  child?: boolean
  sel: string
  unread: boolean
  expanded: Record<string, boolean>
  onExpand: (url: string) => void
  onSelect: (uid: string) => void
  onOpen: () => void
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
  const failing = (d?.sections || []).flatMap((s) => s.prs || []).filter((x) => tone(x.checks) === 'changes').length
  const drafts = (d?.sections || []).flatMap((s) => s.prs || []).filter((x) => x.isDraft).length

  if (d && !d.fetchedAt && !d.sections?.length) {
    return (
      <div className="list scroll">
        <div className="splash">
          <div className="logo">
            <img src="/head.png" alt="" />
          </div>
          <div style={{ color: 'var(--amber)', fontWeight: 600 }}>
            {SPINNER[Math.floor(p.now / 125) % SPINNER.length]} fetching pull requests…
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
        <div className="search">
          <span className="mono" style={{ fontSize: 12, color: 'var(--dim3)' }}>
            /
          </span>
          <input id="q" value={p.query} placeholder="filter by title, repo, author" onChange={(e) => p.onQuery(e.target.value)} />
        </div>
        <div className={`chip${p.failing ? ' on' : ''}`} onClick={p.onFailing}>
          CI failing <em>{failing}</em>
        </div>
        <div className="chip" title="toggle drafts in the settings card">
          Drafts <em>{drafts}</em>
        </div>
        <div style={{ flex: 1 }} />
        <div className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
          updated{' '}
          {d?.fetchedAt
            ? ((a: string) => (a === 'now' ? 'just now' : a + ' ago'))(age(new Date(d.fetchedAt * 1000).toISOString()))
            : 'never'}
        </div>
      </div>
      <div className="list scroll">
        {p.secs.map((s) => {
          const open = !p.folded[s.name]
          const colour = SECTION_TONE[s.name] || 'var(--dim)'
          const hint = s.name === 'MINE' ? mineNote(s.prs) || SECTION_HINT[s.name] : SECTION_HINT[s.name] || ''
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
              <div className="head" data-fold={s.name} onClick={() => p.onFold(s.name)}>
                <span className="caret">{open ? '▾' : '▸'}</span>
                <span className="label" style={{ color: colour }}>
                  {s.name}
                </span>
                <span className="count">{s.prs.length}</span>
                <div className="fill" />
                <span className="hint">{hint}</span>
              </div>
              {open ? (
                s.error ? (
                  <div className="none err">{s.error.split('\n')[0]}</div>
                ) : !s.prs.length ? (
                  <div className="none">
                    {s.name === 'REVIEWED' && settings(d).window ? `none in the last ${settings(d).window}h` : 'none'}
                  </div>
                ) : (
                  rows.map((row) => {
                    const label = row.team || ''
                    const sep = labels.size > 1 && label !== seen ? ((seen = label), true) : false
                    return (
                      <div key={row.uid}>
                        {sep ? (
                          <div className="group">
                            ─ {label || 'not bound to a team'} <i />
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
                              />
                            ))
                          : null}
                      </div>
                    )
                  })
                )
              ) : null}
            </div>
          )
        })}
        {p.secs.length ? null : <div className="none">nothing fetched yet</div>}
      </div>
    </>
  )
}
