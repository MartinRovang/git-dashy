import { Pinata } from './Pinata'
import { useNow } from '../usePoll'
import type { StateData } from '../types'
import type { VisSection } from '../board'

type Props = {
  data: StateData | null
  /** What the filters left on screen — the counts beside the brand describe this, not the raw board. */
  secs: VisSection[]
  onRefresh: () => void
  onAuto: () => void
  onMenu: () => void
  onUpdate: () => void
  onHelp: () => void
  onFollow: () => void
  onLogo: () => void
  view: 'board' | 'graph'
  onView: (v: 'board' | 'graph') => void
}

/** "Ns" to the next refresh: the only ticking text, so only it re-renders every second, not its bar. */
export function Countdown({ at, interval }: { at: number; interval: number }) {
  const now = useNow(1000)
  return <>{Math.max(0, Math.round(interval - (now / 1000 - at)))}s</>
}

/** The 44px bar: brand, counts, the refresh state, and the actions the whole app can take. */
export function TopBar({ data: d, secs, onRefresh, onAuto, onMenu, onUpdate, onHelp, onFollow, onLogo, view, onView }: Props) {
  const running = d?.running || 0
  // ponytail: both numbers come off the same list. Counting PRs after the filters and repos before
  // them read as "3 PRs · 12 repos", which is two answers to one question.
  const shown = secs.flatMap((x) => x.prs)
  const total = shown.length
  const repos = new Set(shown.map((p) => p.repo)).size
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
      </div>
      <div className="views" title="switch view (G)">
        <div className="vtabs">
          {(['board', 'graph'] as const).map((v) => (
            <button key={v} className={view === v ? 'on' : ''} onClick={() => onView(v)}>
              {v}
            </button>
          ))}
        </div>
        <kbd className="hint">G</kbd>
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
      ) : null}
      <span className="ib" title="refresh now (f)" onClick={onRefresh}>
        ⟳
        <kbd className="hint">f</kbd>
      </span>
      <div className={`toggle${d?.auto ? ' on' : ''}`} title="auto-run on new PRs (a)" onClick={onAuto}>
        <div className="track">
          <i />
        </div>
        <span>AUTO</span>
        <kbd className="hint">a</kbd>
      </div>
      <Pinata />
      <button className="ghost" title="follow a user: a floating card of what they are working on" onClick={onFollow}>
        + follow
      </button>
      <button className="ghost" title="keyboard shortcuts (?)" onClick={onHelp}>
        <kbd className="hint">?</kbd> shortcuts
      </button>
      <span className="ib" title="menu (esc)" onClick={onMenu}>
        ☰
        <kbd className="hint">esc</kbd>
      </span>
    </div>
  )
}
