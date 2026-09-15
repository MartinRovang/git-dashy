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
  onReport: (op: 'start' | 'open') => void
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

/** A setting that holds several at once, as the badges the expanded rail uses for the same thing.
 *
 * ponytail: joined with commas these ran off the narrow rail and you saw "review, cave…". One badge
 * per value, stacked, so every active one is readable at any width.
 */
function Pills({ label, values }: { label: string; values: string[] }) {
  return (
    <span className="ln">
      <em>{label}</em>
      {values.length ? (
        <span className="pills">
          {values.map((v) => (
            <i key={v}>{v}</i>
          ))}
        </span>
      ) : (
        <s className="off">none</s>
      )}
    </span>
  )
}

/** A settings group: a caret and a one-line summary when open, a stacked digest when collapsed.
 *
 * ponytail: the collapsed rail shows a digest. A column of icons tells you which group to click and
 * nothing about what it holds; label/value pairs tell you the model you are reviewing with without
 * expanding anything.
 */
function Group({
  k,
  label,
  summary,
  digest,
  open,
  onToggle,
  collapsed,
  children,
}: {
  k: string
  label: string
  summary: string
  digest: React.ReactNode
  open: boolean
  onToggle: () => void
  collapsed: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`sgrp${open && !collapsed ? ' open' : ''}`}>
      {/* ponytail: on the narrow rail the fields cannot render, so the click opens the rail ONTO this
          group instead of toggling a body nobody can see. */}
      <button
        className="summary"
        title={collapsed ? `${label} — open the rail here` : undefined}
        aria-expanded={open && !collapsed}
        onClick={onToggle}
      >
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
export function Sidebar({ data: d, setting, onPath, onTeams, onModal, onAuto, onAskAgain, onReport, collapsed, onCollapse }: Props) {
  const s = d?.settings || {}
  const o = d?.options || { model: [], depth: [], effort: [], voice: [], hunter: [], subs: [], window: [], interval: [], theme: [], scopes: [] }
  const k = d?.knowledge || { memory: '', store: '', teams: [], teamError: '', notes: [], waiting: [] }
  const rep = k.report
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  // each group opens and shuts on its own; the rail scrolls if you open them all
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const flip = (name: string) => {
    if (collapsed) {
      // the rail is 106px: the fields have nowhere to render, so widen it and land on this group
      setOpen((o) => ({ ...o, [name]: true }))
      onCollapse()
      return
    }
    setOpen((o) => ({ ...o, [name]: !o[name] }))
  }
  // ponytail: `waiting` is what a NO is holding back, not what is waiting for an answer — the rows
  // below are the "ask again" ones. Anything still asking is in d.asks, and the launch dialog owns it.
  const held = k.waiting || []
  const teamList = k.teams.map((t) => t.key + (t.arrived ? ` +${t.arrived}` : ''))
  const teams = teamList.join(', ')
  const win = s.window == null ? 'all' : span(s.window)

  return (
    <div className={`side${collapsed ? ' shut' : ''}`}>
      {/* ponytail: the drag grip stays on the expanded rail. A narrow rail is a fixed shelf, not a
          width you tune, so dragging it is the one gesture that would fight the collapse. */}
      {collapsed ? null : <div className="grip" data-grip="side" />}
      <div className="sh">
        <button className="iconbtn" title={collapsed ? 'Expand sidebar (S)' : 'Collapse sidebar (S)'} onClick={onCollapse}>
          ≡
          {collapsed ? null : <kbd className="hint">S</kbd>}
        </button>
      </div>

      <div className="snav scroll">
        <div className="grpname">Reviewer</div>

        <Group
          k="agent"
          label="Agent"
          summary={[s.model, s.depth, s.effort].filter(Boolean).join(' · ')}
          digest={
            <>
              <Ln label="model" value={s.model || '—'} />
              <Ln label="depth" value={s.depth || 'default'} />
              <Ln label="effort" value={s.effort || 'default'} off={!s.effort} />
              <Pills label="voices" values={s.voice || []} />
              <Pills label="hunters" values={s.hunter || []} />
              <Ln label="auto-run" value={d?.auto ? 'on' : 'off'} off={!d?.auto} />
            </>
          }
          open={!!open.agent}
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
            <kbd className="hint">x</kbd> voices <em>active</em>
          </div>
          <Chips values={s.voice || []} options={o.voice} onToggle={(v) => setting('voice', toggle(s.voice || [], v))} />
          <div className="sub">
            <kbd className="hint">h</kbd> hunters <em>active</em>
          </div>
          <Chips values={s.hunter || []} options={o.hunter} onToggle={(v) => setting('hunter', toggle(s.hunter || [], v))} />
          {/* ponytail: auto lives with the agent now, which is what it configures. The top bar keeps
              its switch too — it is the one setting you flip mid-session without opening anything. */}
          <button className="fld" aria-pressed={!!d?.auto} onClick={onAuto}>
            <kbd className="hint">a</kbd>
            <span>Auto-run on new PRs</span>
            <span className="sw" />
          </button>
        </Group>

        <Group
          k="view"
          label="View"
          summary={`${win} history · ${every(s.interval || 0)}`}
          digest={
            <>
              <Ln label="history" value={win} />
              <Ln label="refresh" value={every(s.interval || 0)} />
              <Ln label="drafts" value={s.drafts ? 'shown' : 'hidden'} off={!s.drafts} />
              <Ln label="key hints" value={s.keyhints === false ? 'hidden' : 'shown'} off={s.keyhints === false} />
            </>
          }
          open={!!open.view}
          onToggle={() => flip('view')}
          collapsed={collapsed}
        >
          <Row k="s" label="summaries">
            <Select value={s.subs || ''} options={o.subs} show={(v) => v} onChange={(v) => setting('subs', v)} />
          </Row>
          <button className="fld" aria-pressed={!!s.drafts} onClick={() => setting('drafts', !s.drafts)}>
            <kbd className="hint">D</kbd>
            <span>Show drafts</span>
            <span className="sw" />
          </button>
          {/* ponytail: .hidekeys has been in the stylesheet with nothing setting it. This is the
              switch it was waiting for, and the sheet carries the same one. */}
          <button className="fld" aria-pressed={s.keyhints !== false} onClick={() => setting('keyhints', s.keyhints === false)}>
            <span>Show key hints</span>
            <span className="sw" />
          </button>
          <Row k="O" label="sources">
            {o.scopes.length ? (
              <Chips values={s.scopes || []} options={o.scopes} onToggle={(v) => setting('scopes', toggle(s.scopes || [], v))} />
            ) : (
              <b style={{ color: 'var(--dim2)' }}>none seen yet</b>
            )}
          </Row>
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
          summary={[teams || 'no team', held.length ? `${held.length} held back` : ''].filter(Boolean).join(' · ')}
          digest={
            <>
              <Ln label="memory" value={k.memory || 'default'} />
              <Pills label="teams" values={teamList} />
              {k.store ? <Ln label="store" value={k.store} /> : null}
              {/* ponytail: a refused consent is a nudge, so it survives the collapse as a count.
                  Everything else in this digest is a setting; this one is something to go and undo. */}
              <Ln label="held back" value={String(held.length)} off={!held.length} />
            </>
          }
          open={!!open.know}
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
          {/* What a "no" is still holding back. Clicking one asks that question again. */}
          {held.map((w, i) => (
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
                  {label} <kbd className="hint">{key}</kbd>
                </button>
              ),
            )}
          </div>
        </Group>

        <Group
          k="tools"
          label="Tools"
          summary={rep?.job.running ? 'writing Friday report…' : rep?.latest ? `report ${rep.latest}` : 'Friday report'}
          digest={<Ln label="report" value={rep?.job.running ? 'writing…' : rep?.latest || 'none'} off={!rep?.latest && !rep?.job.running} />}
          open={!!open.tools}
          onToggle={() => flip('tools')}
          collapsed={collapsed}
        >
          <div className="sub">
            Friday report <em>7 days</em>
          </div>
          <div className="tags">
            {/* one report a session: once it is written, open is all there is to do */}
            {rep?.job.running ? (
              <button className="tag" disabled>
                writing… {rep.job.elapsed || 0}s
              </button>
            ) : rep?.latest ? (
              <button className="tag" title={`open the ${rep.latest} report in the browser`} onClick={() => onReport('open')}>
                open {rep.latest}
              </button>
            ) : (
              <button className="tag" onClick={() => onReport('start')}>
                generate
              </button>
            )}
          </div>
          {rep?.job.error ? <div className="note">⚠ report: {rep.job.error}</div> : null}
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
