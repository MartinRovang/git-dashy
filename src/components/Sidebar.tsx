import type { StateData } from '../types'
import type { VisSection } from '../board'
import { counts } from '../board'
import { SECTION_TONE } from '../tokens'

type Props = { data: StateData | null; secs: VisSection[]; onJump: (name: string) => void }

/** The left rail: sections to jump to, then this session's verdict counts. */
export function Sidebar({ data, secs, onJump }: Props) {
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
        {counts(data).map(([l, n, c]) => (
          <div key={l} className="stat">
            <span>{l}</span>
            <b style={{ color: c }}>{n}</b>
          </div>
        ))}
      </div>
    </div>
  )
}
