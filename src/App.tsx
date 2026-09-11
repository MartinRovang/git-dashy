import { useEffect, useMemo, useState } from 'react'
import { errorText, post } from './api'
import { flat, selected, visible } from './board'
import { Queue } from './components/Queue'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { useNow, useStatePoll } from './usePoll'

/** The dashboard: one poll of /api/state, and the queue derived from it. */
export default function App() {
  const [data, reload] = useStatePoll(2000)
  const now = useNow(1000)
  const [sel, setSel] = useState('')
  const [query, setQuery] = useState('')
  const [failing, setFailing] = useState(false)
  const [folded, setFolded] = useState<Record<string, boolean>>({})
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [flash, setFlash] = useState('')

  const secs = useMemo(() => visible(data, query, failing), [data, query, failing])
  const rows = useMemo(() => flat(secs, folded, expanded), [secs, folded, expanded])
  const total = secs.reduce((n, s) => n + s.prs.length, 0)
  const running = data?.running || 0

  useEffect(() => {
    if (!flash) return
    const id = setTimeout(() => setFlash(''), 4000)
    return () => clearTimeout(id)
  }, [flash])

  async function call(path: string, body?: unknown, okMsg?: string) {
    const r = await post(path, body)
    if (!r.ok) {
      setFlash(`✗ ${await errorText(r)}`)
      return null
    }
    const out = await r.json().catch(() => null)
    if (okMsg) setFlash(okMsg)
    reload()
    return out
  }

  const onRefresh = () => call('/api/refresh', {}, 'refreshing…')
  const onAuto = () => {
    const on = !data?.auto
    const includeExisting = on && data?.pending ? confirm(`Auto on. Also review the ${data.pending} already listed?`) : false
    call('/api/auto', { on, includeExisting }, on ? 'auto on' : 'auto off')
  }
  const onJump = (name: string) => {
    document.querySelector(`[data-fold="${name}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }

  const current = selected(rows, sel)
  const selUid = current?.uid || ''

  return (
    <div id="app">
      <TopBar data={data} now={now} total={total} onRefresh={onRefresh} onAuto={onAuto} />
      {(data?.notices || []).map((n) => (
        <div className="notice" key={n}>
          {n}
          <span className="x" onClick={() => call('/api/notices')}>
            ✕ dismiss
          </span>
        </div>
      ))}
      <div className="body">
        <Sidebar data={data} secs={secs} onJump={onJump} />
        <div className="main">
          <div className="queue">
            <Queue
              data={data}
              secs={secs}
              now={now}
              sel={selUid}
              query={query}
              onQuery={setQuery}
              failing={failing}
              onFailing={() => setFailing((v) => !v)}
              folded={folded}
              expanded={expanded}
              onFold={(name) => setFolded((f) => ({ ...f, [name]: !f[name] }))}
              onExpand={(url) => setExpanded((e) => ({ ...e, [url]: !e[url] }))}
              onSelect={setSel}
            />
          </div>
          <div className="hints">
            <div className="g">
              <b>NAV</b>
              <span>j/k move · ⏎ pane · 1/2/⇥ tabs · o open · ␣ fold · / filter</span>
            </div>
            <div className="g">
              <b>RUN</b>
              <span>r review · p pre-review · Y open pre-review · a auto</span>
            </div>
            <div className="g">
              <b>CONFIG</b>
              <span>m model · d depth · e effort · x voices · h hunters · i interval</span>
            </div>
            <div className="g">
              <b>APP</b>
              <span>Z dream · f refresh · v view · T team · u update · esc menu · q quit</span>
            </div>
            <div className={`status${flash ? ' flash' : ''}`}>
              {flash || (running ? `${running} running` : `${total} PRs in view`)}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
