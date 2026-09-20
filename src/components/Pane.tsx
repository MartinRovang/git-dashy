import { useState, type ReactNode } from 'react'
import { ListFilter } from 'lucide-react'
import type { DbImpact, Detail, Layout, Row } from '../types'
import { Out, SecFilter, useSections } from '../sections'
import { age, avatar, CHECK_TONE, FINDING_TONE, PALETTE, rowState, tone, when } from '../tokens'
import { useNow } from '../usePoll'
import { clean } from '../dbgraph'
import { DbGraph } from './DbGraph'

/** The review's database section: each table the PR touches with its columns, then what could break. */
function Db({ db, number }: { db: DbImpact; number: number }) {
  const { tables, risks } = clean(db)
  const n = (k: number, word: string) => `${k} ${word}${k === 1 ? '' : 's'}`
  if (!tables.length && !risks.length) return null
  return (
    <>
      <span className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
        {n(tables.length, 'table')} · {n(risks.length, 'risk')}
      </span>
      <DbGraph db={db} number={number} locked />
      {risks.length ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
          {risks.map((r, i) => (
            <div className="find" key={i}>
              <i style={{ background: 'var(--red)' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="tag" style={{ color: 'var(--red)' }}>
                    {(r.kind || 'risk').toUpperCase()}
                  </span>
                  <span className="loc">{(r.loc || '').split('/').pop()}</span>
                </div>
                <p>{r.text}</p>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </>
  )
}

const SECS: [string, string][] = [
  ['about', 'ABOUT'],
  ['checks', 'CHECKS'],
  ['review', 'AI REVIEW'],
  ['database', 'DATABASE'],
  ['pre', 'PRE-REVIEW'],
]
/** The side pane: the selected PR's summary, checks and review. */
export function Pane({
  p,
  detail,
  subs,
  saved,
  onCode,
  onOptions,
  onClose,
  hidden,
  loading,
}: {
  p: Row | null
  detail: Detail | null
  subs: string
  /** the layout from settings; edits here are kept locally and posted quietly, so a drag does not wait on a reload */
  saved: Layout | undefined
  onCode: () => void
  onOptions: (at: { x: number; y: number }) => void
  onClose: () => void
  /** closed: kept mounted so its popped-out sections stay open */
  hidden?: boolean
  /** no data yet: bars, not "No PR selected" */
  loading?: boolean
}) {
  useNow(p?.busy ? 1000 : 0) // the running label's elapsed time
  const [filtering, setFiltering] = useState(false)
  const { lay, order, sec, switchOff } = useSections('pane', SECS.map(([n]) => n), saved, '.pane')
  if (!p)
    return (
      <div className="pane" style={hidden ? { display: 'none' } : undefined}>
        <div className="bar" />
        <div className="in scroll">
          {loading ? (
            <div className="skel" aria-busy="true">
              {[60, 90, 75, 40].map((w, i) => (
                <i key={i} style={{ width: `${w}%` }} />
              ))}
            </div>
          ) : (
            <div className="prose">No PR selected.</div>
          )}
        </div>
      </div>
    )
  const st = rowState(p)
  const pal = PALETTE[st.key] || PALETTE.idle
  const d = detail && detail.url === p.url ? detail : null
  const rev = d?.review
  const size =
    d && d.add != null ? (
      <>
        <span className="mono" style={{ color: 'var(--green)' }}>
          +{d.add}
        </span>
        <span className="mono" style={{ color: 'var(--red)' }}>
          −{d.del}
        </span>
        <span className="mono" style={{ color: 'var(--dim2)' }}>
          {d.files} files
        </span>
      </>
    ) : null
  const found = rev?.findings || []
  const counts = ['blocking', 'note', 'nit']
    .filter((k) => found.some((f) => f.kind === k))
    .map((k) => (
      <span key={k} className="mono" style={{ fontSize: 11, color: FINDING_TONE[k] }}>
        {found.filter((f) => f.kind === k).length} {k}
      </span>
    ))
  const pre = d?.pre || p.pre
  const shape = rev?.db ? clean(rev.db) : null
  // each section's body, or null when this PR has nothing for it
  const body: Record<string, { extra?: ReactNode; content: ReactNode } | null> = {
    about: {
      content: (
        <>
          {p.summary && (subs === 'all' || (subs === 'open' && p.section !== 'REVIEWED')) ? <div className="prose">{p.summary}</div> : null}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
            <div className="av" style={{ width: 24, height: 24, fontSize: 11, background: avatar(p.author || '?') }}>
              {(p.author || '?')[0].toUpperCase()}
            </div>
            <span style={{ fontSize: 13, color: 'var(--ink2)' }}>{p.author}</span>
            <div style={{ flex: 1 }} />
            <div className="state" style={{ height: 24, background: pal.bg, border: `1px solid ${pal.border}`, color: pal.fg }}>
              {st.label}
            </div>
          </div>
          {size ? <div style={{ display: 'flex', gap: 10, marginTop: 10, fontSize: 11 }}>{size}</div> : null}
          <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
            <span className="lab">BRIEF</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--dim)' }}>
              {d ? d.brief.whose + (d.brief.empty ? ' · none' : '') : '…'}
            </span>
          </div>
        </>
      ),
    },
    checks: d?.checks.length
      ? {
          content: (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
              {d.checks.map((c) => (
                <div className="check" key={c.name}>
                  <div className="ci" style={{ background: CHECK_TONE[c.state] || CHECK_TONE.dim }} />
                  <span>{c.name}</span>
                  <em>{c.state}</em>
                </div>
              ))}
            </div>
          ),
        }
      : null,
    review: rev
      ? {
          extra: (
            <>
              <span className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
                {rev.model} {rev.tag}
              </span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--dim3)', marginLeft: 'auto' }}>
                {age(rev.at)} ago
              </span>
            </>
          ),
          content: (
            <>
              <div style={{ marginTop: 8, display: 'flex', gap: 10, alignItems: 'center' }}>
                <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: PALETTE[tone(rev.verdict)]?.fg || 'var(--amber)' }}>
                  {rev.verdict}
                </span>
                {counts}
                {found.length ? (
                  <span
                    className="mono"
                    style={{ fontSize: 10, color: 'var(--violet)', marginLeft: 'auto', cursor: 'pointer' }}
                    onClick={onCode}
                  >
                    {found.length} in code →
                  </span>
                ) : null}
              </div>
              {found.length ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
                  {found.map((f, i) => (
                    <div className="find" key={i}>
                      <i style={{ background: FINDING_TONE[f.kind] || CHECK_TONE.dim }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span className="tag" style={{ color: FINDING_TONE[f.kind] || CHECK_TONE.dim }}>
                            {f.kind.toUpperCase()}
                          </span>
                          <span className="loc">{(f.loc || '').split('/').pop()}</span>
                        </div>
                        <p>{f.text}</p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : rev.summary ? (
                <div className="prose">{rev.summary}</div>
              ) : null}
            </>
          ),
        }
      : null,
    database:
      rev?.db && shape && (shape.tables.length || shape.risks.length) ? { content: <Db db={rev.db} number={p.number} /> } : null,
    pre: pre
      ? {
          extra: (
            <span className="mono" style={{ fontSize: 11, color: pre.moved ? 'var(--red)' : 'var(--dim)' }}>
              {pre.moved ? '· stale, the PR moved since' : '· current'}
            </span>
          ),
          content: (
            <div className="mono" style={{ fontSize: 11, color: 'var(--dim2)', marginTop: 6 }}>
              {when(pre.at)} — p to read, Y to open
            </div>
          ),
        }
      : null,
  }
  const label = Object.fromEntries(SECS)
  // ponytail: while the detail is still on its way, every section keeps its place with a bar in it,
  // so the pane stops rearranging itself as each one lands. Once it is here, the empty ones go.
  const waiting = !d || d.pending
  const shown = order.filter((k) => (body[k] || waiting) && !lay.off?.includes(k))
  return (
    <div className="pane" style={hidden ? { display: 'none' } : undefined}>
      <div className="grip" data-grip="pane" />
      <div className="bar">
        <span className="lab">SELECTED</span>
        <span className="branch mono" title={d?.branch || ''}>
          {d && d.branch ? d.branch : d && d.pending ? 'loading…' : `#${p.number}`}
        </span>
        <div style={{ flex: 1 }} />
        <span className="ib" title="choose which sections the pane shows" aria-pressed={filtering} onClick={() => setFiltering(!filtering)}>
          <ListFilter size={14} />
        </span>
        <button
          className="tab opt"
          aria-haspopup="menu"
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect()
            onOptions({ x: r.right, y: r.bottom + 6 })
          }}
        >
          options <span className="car">▾</span>
        </button>
        <span className="ib" title="hide the pane (⏎)" onClick={onClose}>
          ×<kbd className="hint">⏎</kbd>
        </span>
      </div>
      {filtering ? <SecFilter secs={SECS} off={lay.off} onFlip={switchOff} /> : null}
      <div className="in scroll">
        <div className="crumbs">
          <span style={{ color: 'var(--pink)' }}>#{p.number}</span>
          <span>{p.repo}</span>
          <span style={{ color: 'var(--dim3)' }}>·</span>
          <span>updated {age(p.updatedAt)} ago</span>
        </div>
        <div className="ptitle">{p.title}</div>
        {shown.map((k) => {
          const x = sec(k)
          const { extra, content } = body[k] || {
            extra: undefined,
            content: (
              <div className="skel" aria-busy="true">
                {[70, 45].map((w, i) => (
                  <i key={i} style={{ width: `${w}%` }} />
                ))}
              </div>
            ),
          }
          return (
            <div key={k} className={`psec${x.shut ? ' shut' : ''}${x.cls}`} {...x.wrap}>
              <div
                className="phd"
                {...x.head}
                role="button"
                tabIndex={0}
                aria-expanded={!x.shut && !x.out}
                onClick={x.out ? x.dock : x.fold}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), (x.out ? x.dock : x.fold)())}
                title={x.out ? 'popped out: click to put it back' : 'click to fold, drag to reorder, drag out of the pane to pop it out'}
              >
                <span className="car">▶</span>
                <span className="lab">{label[k]}</span>
                {extra}
              </div>
              {x.out ? (
                <Out id={`pane-${k}`} label={label[k]} onDock={x.dock}>
                  {content}
                </Out>
              ) : x.shut ? null : (
                content
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
