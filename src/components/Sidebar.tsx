import type { StateData } from '../types'
import type { VisSection } from '../board'
import { counts } from '../board'
import { every, SECTION_TONE } from '../tokens'
import { Chips, Row, Select } from './Controls'

type Props = {
  data: StateData | null
  secs: VisSection[]
  onJump: (name: string) => void
  setting: (name: string, value: unknown) => void
  onPath: (which: 'L' | 'C') => void
  onTeams: () => void
  onModal: (name: string) => void
  onAskAgain: (kind: string, key: string) => void
}

/** The left rail: sections to jump to, the session's counts, then the agent, view and knowledge cards. */
export function Sidebar({ data: d, secs, onJump, setting, onPath, onTeams, onModal, onAskAgain }: Props) {
  const s = d?.settings || {}
  const o = d?.options || { model: [], depth: [], effort: [], voice: [], hunter: [], subs: [], window: [], interval: [], theme: [] }
  const k = d?.knowledge || { memory: '', store: '', teams: [], teamError: '', notes: [], waiting: [] }
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  return (
    <div className="side scroll">
      <div className="cap">WORKSPACE</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {secs.map((sec) => (
          <div key={sec.name} className="nav" onClick={() => onJump(sec.name)}>
            <i style={{ background: SECTION_TONE[sec.name] || 'var(--dim3)' }} />
            <b>{sec.name.toLowerCase()}</b>
            <em>{sec.prs.length}</em>
          </div>
        ))}
      </div>
      <div className="rule" />
      <div className="cap">SESSION</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {counts(d).map(([l, n, c]) => (
          <div key={l} className="stat">
            <span>{l}</span>
            <b style={{ color: c }}>{n}</b>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="cap" style={{ padding: '0 0 7px' }}>
          AGENT
        </div>
        <Row k="m" label="model">
          <Select value={s.model || ''} options={o.model} show={(v) => v} onChange={(v) => setting('model', v)} />
        </Row>
        <Row k="d" label="depth">
          <Select value={s.depth || ''} options={o.depth} show={(v) => v || 'default'} onChange={(v) => setting('depth', v)} />
        </Row>
        <Row k="e" label="effort">
          <Select value={s.effort || ''} options={o.effort} show={(v) => v || 'default'} onChange={(v) => setting('effort', v)} />
        </Row>
        <Row k="x" label="voices">
          <Chips values={s.voice || []} options={o.voice} onToggle={(v) => setting('voice', toggle(s.voice || [], v))} />
        </Row>
        <Row k="h" label="hunters">
          <Chips values={s.hunter || []} options={o.hunter} onToggle={(v) => setting('hunter', toggle(s.hunter || [], v))} />
        </Row>
      </div>

      <div className="card">
        <div className="cap" style={{ padding: '0 0 7px' }}>
          VIEW
        </div>
        <Row k="s" label="summaries">
          <Select value={s.subs || ''} options={o.subs} show={(v) => v} onChange={(v) => setting('subs', v)} />
        </Row>
        <Row k="D" label="drafts">
          <b className={`link${s.drafts ? ' on' : ''}`} onClick={() => setting('drafts', !s.drafts)}>
            {s.drafts ? 'shown' : 'hidden'}
          </b>
        </Row>
        <Row k="t" label="history">
          <Select
            value={s.window == null ? 'all' : String(s.window)}
            options={o.window.map((v) => (v == null ? 'all' : String(v)))}
            show={(v) => (v === 'all' ? 'all' : `${v}h`)}
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
        <Row k="a" label="auto">
          <b style={{ color: d?.auto ? 'var(--green)' : 'var(--dim2)' }}>{d?.auto ? 'on' : 'off'}</b>
        </Row>
      </div>

      <div className="card">
        <div className="cap" style={{ padding: '0 0 7px' }}>
          KNOWLEDGE
        </div>
        <Row k="L" label="memory">
          <b className="link" title={k.memory} onClick={() => onPath('L')}>
            {k.memory}
          </b>
        </Row>
        <Row k="T" label="team">
          <b className={`link${k.teamError ? ' err' : k.teams.length ? ' on' : ''}`} title={k.teamError} onClick={onTeams}>
            {k.teamError ? k.teamError.slice(0, 40) : k.teams.map((t) => t.key + (t.arrived ? ` +${t.arrived}` : '')).join(', ') || 'off'}
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
        {/* Both consent gates are asked once at launch and never again, so a team joined since you
            started, an agents.md a teammate pushed, and an answer given by accident all leave
            something withheld. These say what, and clicking one asks the question again, now. */}
        {(k.waiting || []).map((w, i) => (
          <div className="note link" key={`w${i}`} title="ask me again" onClick={() => onAskAgain(w.kind, w.key)}>
            ⚠ {w.key}: {w.what} — ask again
          </div>
        ))}
        <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
          <span className="btn" onClick={() => onModal('drafts')}>
            <kbd className="hint">W</kbd>waiting
          </span>
          <span className="btn" onClick={() => onModal('share')}>
            <kbd className="hint">P</kbd>shared
          </span>
          <span className="btn" onClick={() => onModal('dream')}>
            <kbd className="hint">Z</kbd>dream
          </span>
          <span className="btn" onClick={() => onModal('general')}>
            <kbd className="hint">g</kbd>general
          </span>
        </div>
      </div>
      <div className="grip" data-grip="side" />
    </div>
  )
}
