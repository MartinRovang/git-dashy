import { Pinata } from './Pinata'
import type { StateData } from '../types'

type Props = {
  data: StateData | null
  now: number
  total: number
  onRefresh: () => void
  onAuto: () => void
  onMenu: () => void
  onUpdate: () => void
  onHelp: () => void
  onLogo: () => void
  view: 'board' | 'graph'
  onView: (v: 'board' | 'graph') => void
}

/** The 44px bar: brand, counts, the refresh state, and the actions the whole app can take. */
export function TopBar({ data: d, now, total, onRefresh, onAuto, onMenu, onUpdate, onHelp, onLogo, view, onView }: Props) {
  const running = d?.running || 0
  const repos = new Set((d?.sections || []).flatMap((s) => s.prs || []).map((p) => p.repo)).size
  const left = d?.fetchedAt ? Math.max(0, Math.round(d.interval - (now / 1000 - d.fetchedAt))) : 0
  return (
    <div className="top">
      <button className="logo" title="play the intro" aria-label="toggle the player" onClick={onLogo}>
        <img src="/head.png" alt="" />
      </button>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="brand">gitdashy</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
          v{d?.version || ''}
        </span>
      </div>
      <div className="vbar" />
      <div className="ctx">
        <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{total} PRs</span>
        <i className="dot" />
        {/* ponytail: repos, not orgs. The board spans whatever the token can see, and "4 repos" is the
            number that tells you whether a queue looks short because it is, or because you are
            pointed at less than you thought. */}
        <span>{repos} repo{repos === 1 ? '' : 's'}</span>
        <i className="dot" />
        <span>{d?.model || ''}</span>
      </div>
      <div className="vtabs" title="switch view (G)">
        {(['board', 'graph'] as const).map((v) => (
          <button key={v} className={view === v ? 'on' : ''} onClick={() => onView(v)}>
            {v}
          </button>
        ))}
      </div>
      <div style={{ flex: 1 }} />
      {d?.update ? (
        <div className="pill up" onClick={onUpdate}>
          <kbd className="hint">u</kbd> ↑ update to v{d.update}
        </div>
      ) : null}
      <div className={`pill${running ? ' busy' : ''}`}>
        <span>{running ? `${running} agent${running > 1 ? 's' : ''} running` : 'agents idle'}</span>
      </div>
      {d?.error ? (
        <div className="pill err" title={d.error}>
          ✗ refresh failed: {d.error.slice(0, 40)}
        </div>
      ) : (
        <div className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
          {!d?.fetchedAt ? 'fetching…' : d?.fetching ? 'refreshing…' : `refresh in ${left}s`}
        </div>
      )}
      <span className="ib" title="refresh now (f)" onClick={onRefresh}>
        ⟳
      </span>
      <div className={`toggle${d?.auto ? ' on' : ''}`} onClick={onAuto}>
        <div className="track">
          <i />
        </div>
        <span>AUTO</span>
      </div>
      <Pinata />
      <button className="ghost" title="keyboard shortcuts (?)" onClick={onHelp}>
        <kbd>?</kbd> shortcuts
      </button>
      <span className="ib" title="menu (esc)" onClick={onMenu}>
        ☰
      </span>
    </div>
  )
}
