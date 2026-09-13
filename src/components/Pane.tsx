import type { Detail, Row } from '../types'
import { age, avatar, CHECK_TONE, FINDING_TONE, PALETTE, rowState, tone, when } from '../tokens'

type Act = [key: string, cls: string, label: string, note: string, off: boolean, act: string]

function actsHTML(p: Row, d: Detail | null): Act[] {
  const acts: Act[] = []
  const rr = p.section === 'REVIEW REQUESTED'
  const mine = p.section === 'MINE'
  const reviewed = !!(p.review && !p.busy && tone(p.review))
  if (rr)
    acts.push(['r', 'go', p.busy ? 'Reviewing…' : reviewed ? 'Reviewed' : 'Review this PR', '', p.busy || reviewed, 'review'])
  if (mine)
    acts.push([
      'p',
      '',
      p.pre?.moved ? 'Re-run the pre-review' : p.pre ? 'Read the pre-review' : 'Pre-review',
      p.pre?.moved ? 'the PR moved since' : 'nothing posted',
      p.busy,
      'pre',
    ])
  if (mine && p.pre) acts.push(['Y', '', 'Open the pre-review', 'desktop .md handler', false, 'openpre'])
  if (d?.review) acts.push(['v', '', 'Read the full review', d.review.model, false, 'view'])
  acts.push(['o', '', 'Open in browser', 'github', false, 'open'])
  acts.push(['y', '', 'Copy the URL', 'clipboard', false, 'copy'])
  if (mine) acts.push(['+', '', 'Request a review', 'pick a collaborator', false, 'reviewer'])
  acts.push(['b', '', 'Bind the repo to a team', d?.brief?.whose || '', false, 'bind'])
  acts.push(['n', '', "Edit this repo's memory", (p.repo || '').split('/').pop() || '', false, 'memory'])
  return acts
}

/** The side pane: the selected PR's summary or its review pinned to the code. */
export function Pane({
  p,
  detail,
  subs,
  onCode,
  onAct,
  onClose,
}: {
  p: Row | null
  detail: Detail | null
  subs: string
  onCode: () => void
  onAct: (name: string) => void
  onClose: () => void
}) {
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
        <span className="lab">SELECTED PR</span>
        <span className="tab" onClick={onCode}>
          <kbd className="hint">2</kbd>view code
        </span>
        <div style={{ flex: 1 }} />
        <div className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
          {d && d.branch ? d.branch : d && d.pending ? 'loading…' : ''}
        </div>
        <span className="ib" title="hide the pane (⏎)" onClick={onClose}>
          ×
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
        <>
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
          <div className="sep" />
          <div className="lab">ACTIONS</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
            {actsHTML(p, d).map(([k, cls, label, note, off, act]) => (
              <div key={act} className={`act ${cls}${off ? ' off' : ''}`} onClick={() => !off && onAct(act)}>
                <kbd className="hint">{k}</kbd>
                <b>{label}</b>
                <em>{note}</em>
              </div>
            ))}
          </div>
        </>
      </div>
    </div>
  )
}