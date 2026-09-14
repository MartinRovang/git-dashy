import type { Detail, Row } from '../types'
import { age, avatar, CHECK_TONE, FINDING_TONE, PALETTE, rowState, tone, when } from '../tokens'
import { useNow } from '../usePoll'

/** The side pane: the selected PR's summary, checks and review. */
export function Pane({
  p,
  detail,
  subs,
  onCode,
  onOptions,
  onClose,
}: {
  p: Row | null
  detail: Detail | null
  subs: string
  onCode: () => void
  onOptions: (at: { x: number; y: number }) => void
  onClose: () => void
}) {
  useNow(p?.busy ? 1000 : 0) // the running label's elapsed time
  if (!p)
    return (
      <div className="pane">
        <div className="bar" />
        <div className="in scroll">
          <div className="prose">No PR selected.</div>
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
        <span className="mono" style={{ color: 'var(--green)' }}>+{d.add}</span>
        <span className="mono" style={{ color: 'var(--red)' }}>−{d.del}</span>
        <span className="mono" style={{ color: 'var(--dim2)' }}>{d.files} files</span>
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
  return (
    <div className="pane">
      <div className="grip" data-grip="pane" />
      <div className="bar">
        <span className="lab">SELECTED</span>
        <span className="branch mono" title={d?.branch || ''}>
          {d && d.branch ? d.branch : d && d.pending ? 'loading…' : `#${p.number}`}
        </span>
        <div style={{ flex: 1 }} />
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
          ×
          <kbd className="hint">⏎</kbd>
        </span>
      </div>
      <div className="in scroll">
        <div className="crumbs">
          <span style={{ color: 'var(--pink)' }}>#{p.number}</span>
          <span>{p.repo}</span>
          <span style={{ color: 'var(--dim3)' }}>·</span>
          <span>updated {age(p.updatedAt)} ago</span>
        </div>
        <div className="ptitle">{p.title}</div>
        {p.summary && (subs === 'all' || (subs === 'open' && p.section !== 'REVIEWED')) ? (
          <div className="prose">{p.summary}</div>
        ) : null}
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
        {d?.checks.length ? (
          <>
            <div className="sep" />
            <div className="lab">CHECKS</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
              {d.checks.map((c) => (
                <div className="check" key={c.name}>
                  <div className="ci" style={{ background: CHECK_TONE[c.state] || CHECK_TONE.dim }} />
                  <span>{c.name}</span>
                  <em>{c.state}</em>
                </div>
              ))}
            </div>
          </>
        ) : null}
        {rev ? (
          <>
            <div className="sep" />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="lab">AI REVIEW</span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
                {rev.model} {rev.tag}
              </span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--dim3)', marginLeft: 'auto' }}>
                {age(rev.at)} ago
              </span>
            </div>
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
        ) : null}
        {pre ? (
          <>
            <div className="sep" />
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="lab">PRE-REVIEW</span>
              <span className="mono" style={{ fontSize: 11, color: pre.moved ? 'var(--red)' : 'var(--dim)' }}>
                {pre.moved ? '· stale, the PR moved since' : '· current'}
              </span>
            </div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--dim2)', marginTop: 6 }}>
              {when(pre.at)} — p to read, Y to open
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}