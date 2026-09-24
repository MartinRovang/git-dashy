import { useEffect, useRef, useState } from 'react'
import { Pinata } from './Pinata'
import { hostApps, type HostApp } from '../host'
import { useNow } from '../usePoll'
import type { StateData } from '../types'
import type { Only, VisSection } from '../board'

type Props = {
  data: StateData | null
  /** What the filters left on screen — the counts beside the brand describe this, not the raw board. */
  secs: VisSection[]
  spinning: boolean
  onRefresh: () => void
  onAuto: () => void
  onMenu: () => void
  onUpdate: () => void
  onHelp: () => void
  view: 'board' | 'graph' | 'necronomicon'
  onView: (v: 'board' | 'graph' | 'necronomicon') => void
  /** The repo and author picks; empty is all. */
  only: Only
  /** False when a picker would open empty (no data yet, or nothing on the board). */
  canPick: (which: keyof Only) => boolean
  onOnly: (which: keyof Only) => void
  onClearOnly: (which: keyof Only) => void
}

/** The logo. Inside icecream it opens the app switcher: both apps, the one on screen ticked. */
function AppMenu() {
  const apps = hostApps()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => box.current?.contains(e.target as Node) || setOpen(false)
    // captured and stopped: Esc also opens the app's menu, and this Esc is only for closing the switcher
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      setOpen(false)
    }
    document.addEventListener('mousedown', away)
    window.addEventListener('keydown', esc, true)
    return () => {
      document.removeEventListener('mousedown', away)
      window.removeEventListener('keydown', esc, true)
    }
  }, [open])
  const img = <img src="/head.png" alt="" />
  if (!apps) return <span className="logo">{img}</span>
  return (
    <div className="appmenu" ref={box}>
      <button title="switch app" aria-label="switch app" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="logo">{img}</span>
        <span className="caret">▾</span>
      </button>
      {open ? (
        <div className="am-list" role="menu">
          {apps.map((a: HostApp) =>
            a.current ? (
              <div key={a.id} className="am-item on" role="menuitem" aria-current="true">
                <img src={a.icon} alt="" />
                {a.name}
                <b>✓</b>
              </div>
            ) : (
              <a key={a.id} className="am-item" role="menuitem" href={a.href} onClick={() => setOpen(false)}>
                <img src={a.icon} alt="" />
                {a.name}
                <kbd>{a.key}</kbd>
              </a>
            ),
          )}
        </div>
      ) : null}
    </div>
  )
}

/** "Ns" to the next refresh: the only ticking text, so only it re-renders every second, not its bar. */
export function Countdown({ at, interval }: { at: number; interval: number }) {
  const now = useNow(1000)
  return <>{Math.max(0, Math.round(interval - (now / 1000 - at)))}s</>
}

/** The 44px bar: brand, counts, the refresh state, and the actions the whole app can take. */
export function TopBar({ data: d, spinning, secs, onRefresh, onAuto, onMenu, onUpdate, onHelp, view, onView, only, canPick, onOnly, onClearOnly }: Props) {
  const running = d?.running || 0
  // ponytail: both numbers come off the same list. Counting PRs after the filters and repos before
  // them read as "3 PRs · 12 repos", which is two answers to one question.
  const shown = secs.flatMap((x) => x.prs)
  const total = shown.length
  const repos = new Set(shown.map((p) => p.repo)).size
  return (
    <div className="top">
      <AppMenu />
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="brand">gitdashy</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>
          v{d?.version || ''}
        </span>
      </div>
      <div className="vbar" />
      <div className="ctx">
        {/* ponytail: repos, not orgs. The board spans whatever the token can see, and "4 repos" is the
            number that tells you whether a queue looks short because it is, or because you are
            pointed at less than you thought. */}
        {d ? (
          <>
            <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{total} PRs</span>
            <i className="dot" />
            <span>{repos} repo{repos === 1 ? '' : 's'}</span>
          </>
        ) : (
          <i className="sk" style={{ width: '8em' }} />
        )}
      </div>
      <div className="views" title="switch view (G)">
        <div className="vtabs">
          {(['board', 'graph', 'necronomicon'] as const).map((v) => (
            <button key={v} className={view === v ? 'on' : ''} onClick={() => onView(v)}>
              {v}
            </button>
          ))}
        </div>
        <kbd className="hint">G</kbd>
      </div>
      <div className="fgroup">
        {(['repos', 'authors'] as const).map((w) => {
          const n = only[w].length
          return (
            <button key={w} className="chip" aria-pressed={!!n} disabled={!canPick(w)} title={`only these ${w}`} onClick={() => onOnly(w)}>
              {w} <b>{n ? (n === 1 ? only[w][0].split('/').pop() : n) : 'all'}</b>
              {n ? (
                <span
                  aria-label={`clear ${w}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    onClearOnly(w)
                  }}
                >
                  ✕
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
      <div style={{ flex: 1 }} />
      {d?.update ? (
        <div className="pill up" onClick={onUpdate}>
          <kbd className="hint">u</kbd> ↑ update to v{d.update}
        </div>
      ) : null}
      {running ? (
        <div className="pill busy">
          <span>{`${running} agent${running > 1 ? 's' : ''} running`}</span>
        </div>
      ) : null}
      {d?.error ? (
        <div className="pill err" title={d.error}>
          ✗ refresh failed: {d.error.slice(0, 40)}
        </div>
      ) : null}
      <span className="ib" title="refresh now (f)" onClick={onRefresh}>
        {spinning ? <span className="spinner" /> : <span>⟳</span>}
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
