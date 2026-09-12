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
  onLogo: () => void
}

/** The 44px bar: brand, counts, the refresh state, and the actions the whole app can take. */
export function TopBar({ data: d, now, total, onRefresh, onAuto, onMenu, onUpdate, onLogo }: Props) {
  const running = d?.running || 0
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
      <div className="mono" style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11, color: 'var(--dim)' }}>
        <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{total} PRs</span>
        <span style={{ color: 'var(--dim3)' }}>·</span>
        <span>{d?.model || ''}</span>
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
      <span className="ib" title="menu (esc)" onClick={onMenu}>
        ☰
      </span>
    </div>
  )
}
