import { useState } from 'react'
import type { StateData } from '../types'
import { counts } from '../board'
import { every, span } from '../tokens'
import { Chips, Row, Select } from './Controls'

type Props = {
  data: StateData | null
  setting: (name: string, value: unknown) => void
  onPath: (which: 'L' | 'C') => void
  onTeams: () => void
  onModal: (name: string) => void
  onAuto: () => void
  onAskAgain: (kind: string, key: string) => void
  collapsed: boolean
  onCollapse: () => void
}

/** One label/value pair in a collapsed group's stack. */
function Ln({ label, value, off }: { label: string; value: string; off?: boolean }) {
  return (
    <span className="ln">
      <em>{label}</em>
      <s className={off ? 'off' : undefined}>{value}</s>
    </span>
  )
}

/** A settings group: a caret and a one-line summary when open, a stacked digest when collapsed.
 *
 * ponytail: the digest is the whole point of the collapsed rail. A column of icons tells you which
 * group to click and nothing about what it holds; three label/value pairs tell you the model you are
 * reviewing with without expanding anything, which is the question the rail is usually asked.
 */
function Group({
  k,
  label,
  title,
  summary,
  digest,
  open,
  onToggle,
  collapsed,
  children,
}: {
  k: string
  label: string
  title: string
  summary: string
  digest: React.ReactNode
  open: boolean
  onToggle: () => void
  collapsed: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`sgrp${open && !collapsed ? ' open' : ''}`} data-k={k}>
      <button className="summary" title={title} aria-expanded={open && !collapsed} onClick={onToggle}>
        <span className="car">▶</span>
        <span className="ic">{k.toUpperCase()}</span>
        <span className="icv">{digest}</span>
        <span className="lb">{label}</span>
        <span className="sv">{summary}</span>
      </button>
      {open && !collapsed ? <div className="fields">{children}</div> : null}
    </div>
  )
}

/** The left rail: the reviewer's settings as collapsible groups, then the session's outcomes. */
export function Sidebar({ data: d, setting, onPath, onTeams, onModal, onAuto, onAskAgain, collapsed, onCollapse }: Props) {
  const s = d?.settings || {}
  const o = d?.options || { model: [], depth: [], effort: [], voice: [], hunter: [], subs: [], window: [], interval: [], theme: [] }
  const k = d?.knowledge || { memory: '', store: '', teams: [], teamError: '', notes: [], waiting: [] }
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  const [open, setOpen] = useState<string>('agent')
  // ponytail: one group open at a time. Three expanded at 252px is a rail you scroll to read, and the
  // summary line exists so the shut ones still answer for themselves.
  const flip = (name: string) => setOpen((cur) => (cur === name ? '' : name))
  const teams = k.teams.map((t) => t.key + (t.arrived ? ` +${t.arrived}` : '')).join(', ')
  const win = s.window == null ? 'all' : span(s.window)

  return (
    <div className={`side${collapsed ? ' shut' : ''}`}>
      {/* ponytail: the drag grip stays on the expanded rail. A 92px rail is a fixed shelf, not a
          width you tune, so dragging it is the one gesture that would fight the collapse. */}
      {collapsed ? null : <div className="grip" data-grip="side" />}
      <div className="sh">
        <div className="mark">
          <b>gitdashy</b>
          <span>v{d?.version || ''}</span>
        </div>
        <button className="iconbtn" title={collapsed ? 'Expand sidebar (S)' : 'Collapse sidebar (S)'} onClick={onCollapse}>
          ≡
        </button>
      </div>

      <div className="snav scroll">
        <div className="grpname">Reviewer</div>

        <Group
          k="agent"
          label="Agent"
          title={`Reviewing with ${[s.model, s.depth, s.effort].filter(Boolean).join(', ')} · ${(s.voice || []).join(', ') || 'no voices'}`}
          summary={[s.model, s.depth, s.effort].filter(Boolean).join(' · ')}
          digest={
            <>
              <Ln label="model" value={s.model || '—'} />
              <Ln label="depth" value={s.depth || 'default'} />
              <Ln label="voice" value={(s.voice || [])[0] || 'none'} off={!(s.voice || []).length} />
              <Ln label="auto" value={d?.auto ? 'on' : 'off'} off={!d?.auto} />
            </>
          }
          open={open === 'agent'}
          onToggle={() => flip('agent')}
          collapsed={collapsed}
        >
          <Row k="m" label="model">
            <Select value={s.model || ''} options={o.model} show={(v) => v} onChange={(v) => setting('model', v)} />
          </Row>
          <Row k="d" label="depth">
            <Select value={s.depth || ''} options={o.depth} show={(v) => v || 'default'} onChange={(v) => setting('depth', v)} />
          </Row>
          <Row k="e" label="effort">
            <Select value={s.effort || ''} options={o.effort} show={(v) => v || 'default'} onChange={(v) => setting('effort', v)} />
          </Row>
          <div className="sub">
            voices <em>active</em>
          </div>
          <Chips values={s.voice || []} options={o.voice} onToggle={(v) => setting('voice', toggle(s.voice || [], v))} />
          <div className="sub">
            hunters <em>active</em>
          </div>
          <Chips values={s.hunter || []} options={o.hunter} onToggle={(v) => setting('hunter', toggle(s.hunter || [], v))} />
          {/* ponytail: auto lives with the agent now, which is what it configures. The top bar keeps
              its switch too — it is the one setting you flip mid-session without opening anything. */}
          <button className="fld" aria-pressed={!!d?.auto} onClick={onAuto}>
            <span>Auto-run on new PRs</span>
            <span className="sw" />
          </button>
        </Group>

        <Group
          k="view"
          label="View"
          title={`Showing ${s.subs} summaries from ${win}, refreshing ${every(s.interval || 0)}, drafts ${s.drafts ? 'shown' : 'hidden'}`}
          summary={`${win} history · ${every(s.interval || 0)}`}
          digest={
            <>
              <Ln label="history" value={win} />
              <Ln label="refresh" value={every(s.interval || 0)} />
              <Ln label="drafts" value={s.drafts ? 'shown' : 'hidden'} off={!s.drafts} />
            </>
          }
          open={open === 'view'}
          onToggle={() => flip('view')}
          collapsed={collapsed}
        >
          <Row k="s" label="summaries">
            <Select value={s.subs || ''} options={o.subs} show={(v) => v} onChange={(v) => setting('subs', v)} />
          </Row>
          <button className="fld" aria-pressed={!!s.drafts} onClick={() => setting('drafts', !s.drafts)}>
            <span>Show drafts</span>
            <span className="sw" />
          </button>
          <Row k="t" label="history">
            <Select
              value={s.window == null ? 'all' : String(s.window)}
              options={o.window.map((v) => (v == null ? 'all' : String(v)))}
              show={(v) => (v === 'all' ? 'all' : span(+v))}
              onChange={(v) => setting('window', v === 'all' ? null : +v)}
            />
          </Row>
          <Row k="i" label="refresh">
            <Select
              value={String(s.interval)}
              options={o.interval.map(String)}
              show={(v) => every(+v)}
              onChange={(v) => setting('interval', +v)}
            />
          </Row>
        </Group>

        <Group
          k="know"
          label="Knowledge"
          title={`Memory ${k.memory} · teams ${teams || 'none'}`}
          summary={[teams || 'no team', (k.waiting || []).length ? `${(k.waiting || []).length} asking` : ''].filter(Boolean).join(' · ')}
          digest={
            <>
              <Ln label="teams" value={teams || 'none'} off={!k.teams.length} />
              {/* ponytail: a pending consent gate is a nudge, so it survives the collapse as a count.
                  Everything else in this digest is a setting; this one is the only thing asking. */}
              <Ln label="asks" value={String((k.waiting || []).length)} off={!(k.waiting || []).length} />
            </>
          }
          open={open === 'know'}
          onToggle={() => flip('know')}
          collapsed={collapsed}
        >
          <Row k="L" label="memory">
            <b className="link" title={k.memory} onClick={() => onPath('L')}>
              {k.memory}
            </b>
          </Row>
          <Row k="T" label="team">
            <b className={`link${k.teamError ? ' err' : k.teams.length ? ' on' : ''}`} title={k.teamError} onClick={onTeams}>
              {k.teamError ? k.teamError.slice(0, 40) : teams || 'off'}
            </b>
          </Row>
          {k.store ? (
            <Row k="C" label="store">
              <b className="link" onClick={() => onPath('C')}>
                {k.store}
              </b>
            </Row>
          ) : null}
          {(k.notes || []).map((n, i) => (
            <div className="note" key={i}>
              ⚠ {n}
            </div>
          ))}
          {/* What each consent gate is still holding back. Clicking one asks that question again. */}
          {(k.waiting || []).map((w, i) => (
            <div className="note link" key={`w${i}`} title="ask me again" onClick={() => onAskAgain(w.kind, w.key)}>
              ⚠ {w.key}: {w.what} — ask again
            </div>
          ))}
          {/* ponytail: these OPEN things, they do not toggle. The design drew them as on/off tags
              beside the voices, and they are the four screens W / P / Z / g reach. */}
          <div className="sub">
            screens <em>keys</em>
          </div>
          <div className="tags">
            {([['drafts', 'waiting', 'W'], ['share', 'shared', 'P'], ['dream', 'dream', 'Z'], ['general', 'general', 'g']] as const).map(
              ([name, label, key]) => (
                <button className="tag" key={name} onClick={() => onModal(name)}>
                  {label} <kbd>{key}</kbd>
                </button>
              ),
            )}
          </div>
        </Group>
      </div>

      {/* ponytail: the design captions this "last 4h". counts() reads every section, and only REVIEWED
          is cut by the history window — MINE and the two queues are whatever is open. So the caption
          says what the number actually covers. */}
      <div className="sfoot" title="The review state of every PR on the board">
        <div className="hd">
          <b>Status</b>
          <i>on the board</i>
        </div>
        {counts(d).map(([l, n, c]) => (
          <div key={l} className={`stat${n ? '' : ' zero'}`} title={`${n} ${l}`}>
            <i style={{ background: n ? c : 'var(--dim3)' }} />
            <span>{l}</span>
            <b>{n}</b>
          </div>
        ))}
      </div>
    </div>
  )
}
