import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, errorText, post } from './api'
import { flat, selected, visible } from './board'
import { FloatingVideo } from './components/FloatingVideo'
import { Graph } from './components/Graph'
import { Pane } from './components/Pane'
import { Queue } from './components/Queue'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { confirm, modalCount, ModalHost, notice, picker, prompt, repaint, viewer } from './modals'
import type { Ctx } from './screens'
import { askConsents, draftsScreen, dreamScreen, escMenu, memoryEditor, setPath, shareScreen, teamsScreen, updateScreen } from './screens'
import { CONTEXTS, every, tone } from './tokens'
import type { Code, Detail, GraphData, Row, Size, StateData } from './types'
import { useNow, useStatePoll } from './usePoll'

/** The dashboard: one poll of /api/state, the queue derived from it, and the pane's second request. */
export default function App() {
  const [data, reload] = useStatePoll(2000)
  const now = useNow(1000)
  const [sel, setSel] = useState('')
  const [query, setQuery] = useState('')
  const [failing, setFailing] = useState(false)
  const [folded, setFolded] = useState<Record<string, boolean>>({})
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [flash, setFlash] = useState('')
  const [pane, setPane] = useState(true)
  const [video, setVideo] = useState(false)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [diff, setDiff] = useState<Code | null>(null)
  const [tab, setTab] = useState<'summary' | 'code'>('summary')
  const [scope, setScope] = useState('marks')
  const [context, setContext] = useState<number>(CONTEXTS[0])
  const [at, setAt] = useState(0)
  const [stopped, setStopped] = useState(false)
  const [view, setView] = useState<'board' | 'graph'>('board')
  const [graph, setGraph] = useState<GraphData | null>(null)
  // url -> the updatedAt that was read, so a PR that moves goes unread again. Kept in localStorage,
  // which survives a reload but not a relaunch: the GUI picks a new port each launch, so the
  // webview's origin changes and its storage starts empty. A fresh launch therefore opens with
  // everything unread, and reading is one keypress per row.
  // ponytail: no server-side seen-map for that. See the `arrived` note in state.rs.
  const [read, setRead] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(localStorage.getItem('dashy-read') || '{}')
    } catch {
      return {}
    }
  })

  const secs = useMemo(() => visible(data, query, failing), [data, query, failing])
  const rows = useMemo(() => flat(secs, folded, expanded), [secs, folded, expanded])
  const total = secs.reduce((n, s) => n + s.prs.length, 0)
  const current = selected(rows, sel)
  const selUid = current?.uid || ''
  const url = current?.url || ''

  const dataRef = useRef<StateData | null>(null)
  dataRef.current = data
  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {})

  // First run only: most people reach for the terminal, so say the desktop icon exists.
  // ponytail: the flag is a SETTING, not localStorage. The GUI serves itself on a new random port
  // every launch, so the webview's origin changes and its storage starts empty each time.
  const hinted = data?.settings.hinted
  useEffect(() => {
    if (hinted !== false) return // undefined = state has not arrived yet
    void post('/api/settings', { hinted: true })
    void notice(
      <>
        <div style={{ marginBottom: 10 }}>gitdashy installed a desktop icon, open it from there, no terminal needed.</div>
        <img src="/desktop-icon.png" alt="the gitdashy icon on a desktop" style={{ display: 'block' }} />
      </>,
      'welcome',
    )
  }, [hinted])

  useEffect(() => {
    if (!flash) return
    const id = setTimeout(() => setFlash(''), 4000)
    return () => clearTimeout(id)
  }, [flash])

  useEffect(() => {
    if (!current) return
    setRead((r) => {
      if (r[current.url] === current.updatedAt) return r
      const next = { ...r, [current.url]: current.updatedAt }
      try {
        localStorage.setItem('dashy-read', JSON.stringify(next))
      } catch {
        /* storage unavailable */
      }
      return next
    })
  }, [current])

  useEffect(() => {
    document.body.dataset.theme = data?.settings.theme || 'pencil'
  }, [data?.settings.theme])

  // Grip drags: restore the saved widths, then write one CSS var per animation frame while dragging.
  useEffect(() => {
    try {
      const w = JSON.parse(localStorage.getItem('dashy-widths') || '{}')
      for (const k of ['side', 'pane']) if (w[k]) document.documentElement.style.setProperty(`--${k}-w`, w[k])
    } catch {
      /* no saved widths */
    }
    const onDown = (e: PointerEvent) => {
      const grip = (e.target as HTMLElement).closest<HTMLElement>('[data-grip]')
      if (!grip) return
      const which = grip.dataset.grip === 'pane' ? 'pane' : 'side'
      const box = grip.parentElement!
      const start = e.clientX
      const w0 = box.offsetWidth
      // ponytail: writing --pane-w on :root re-inherits the custom property across the whole
      // document, so a full-diff pane re-styles thousands of rows every frame. Write the width on
      // the dragged box instead, and while the pane drags hide the diff: re-laying 8k lines is what
      // makes the drag crawl, and a drag is not the time to do it. The scroll offset is restored on
      // release. (60 frames over 8k rows: 2.9s rooted-var, 1.4s inline, 64ms frozen.)
      const isPane = which === 'pane'
      const scroller = isPane ? box.querySelector<HTMLElement>('.in') : null
      const scrollTop = scroller?.scrollTop ?? 0
      if (isPane) document.documentElement.classList.add('resizing-pane')
      e.preventDefault()
      document.documentElement.classList.add('dragging')
      grip.classList.add('on')
      grip.setPointerCapture(e.pointerId)
      let raf = 0
      let want = w0
      const apply = () => {
        raf = 0
        box.style.width = `${want}px`
      }
      const move = (ev: PointerEvent) => {
        const limit = window.innerWidth / 2
        want = Math.max(160, Math.min(limit, w0 + (which === 'side' ? 1 : -1) * (ev.clientX - start)))
        if (!raf) raf = requestAnimationFrame(apply)
      }
      const up = () => {
        if (raf) {
          cancelAnimationFrame(raf)
          apply()
        }
        grip.classList.remove('on')
        grip.removeEventListener('pointermove', move)
        grip.removeEventListener('pointerup', up)
        grip.removeEventListener('pointercancel', up)
        document.documentElement.classList.remove('dragging')
        if (isPane) {
          document.documentElement.classList.remove('resizing-pane')
          if (scroller) requestAnimationFrame(() => (scroller.scrollTop = scrollTop))
        }
        // keep the root var in step so a remounted pane or sidebar keeps the saved width
        document.documentElement.style.setProperty(`--${which}-w`, `${want}px`)
        try {
          const w = JSON.parse(localStorage.getItem('dashy-widths') || '{}')
          w[which] = `${want}px`
          localStorage.setItem('dashy-widths', JSON.stringify(w))
        } catch {
          /* storage unavailable */
        }
      }
      grip.addEventListener('pointermove', move)
      grip.addEventListener('pointerup', up)
      grip.addEventListener('pointercancel', up)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [])

  // The keyboard, gated on no modal being open (the ModalHost owns keys while it is).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keyRef.current(e)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // The pane's detail: a second request per PR, re-asked while the server reports pending.
  useEffect(() => {
    if (!pane || !url) return
    let alive = true
    let timer: number | undefined
    const run = async () => {
      try {
        const r = await api(`/api/pr?url=${encodeURIComponent(url)}`)
        if (!r.ok) return
        const got = (await r.json()) as Detail
        if (!alive) return
        setDetail(got)
        if (got.pending) timer = window.setTimeout(run, 1500)
      } catch {
        /* the next tick retries */
      }
    }
    run()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [pane, url])

  // The diff, only while the code tab is open.
  useEffect(() => {
    if (!pane || !url || tab !== 'code') return
    let alive = true
    let timer: number | undefined
    const run = async () => {
      try {
        const r = await api(`/api/diff?url=${encodeURIComponent(url)}&scope=${scope}&context=${context}`)
        if (!r.ok) return
        const got = (await r.json()) as Code
        if (!alive) return
        setDiff(got)
        if (got.pending) timer = window.setTimeout(run, 1500)
      } catch {
        /* the next tick retries */
      }
    }
    run()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [pane, url, tab, scope, context])

  // Diff sizes for the graph, only while it is open. Re-asked while pending, and on every state
  // poll after that, so a PR that arrives or moves gets measured too (the server caches per revision).
  useEffect(() => {
    if (view !== 'graph') return
    let alive = true
    let timer: number | undefined
    const run = async () => {
      try {
        const r = await api('/api/graph')
        if (!r.ok) return
        const got = (await r.json()) as GraphData
        if (!alive) return
        setGraph(got)
        if (got.pending) timer = window.setTimeout(run, 1500)
      } catch {
        /* the next tick retries */
      }
    }
    run()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [view, data?.fetchedAt])
  const sizes = useMemo(() => Object.fromEntries((graph?.nodes || []).map((n) => [n.url, n])) as Record<string, Size>, [graph])

  useEffect(() => {
    if (tab !== 'code' || !diff || diff.pending) return
    document.getElementById('jumpto')?.scrollIntoView({ block: 'center' })
  }, [diff, at, tab])

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

  async function setting(name: string, value: unknown) {
    ;(document.activeElement as HTMLElement | null)?.blur()
    const shown = Array.isArray(value)
      ? value.join(', ') || 'off'
      : name === 'interval'
        ? every(Number(value))
        : name === 'window'
          ? value == null
            ? 'all'
            : `${value}h`
          : String(value === '' ? 'default' : value)
    await call('/api/settings', { [name]: value }, `${name} is now ${shown}`)
  }

  async function quit() {
    if (!(await confirm('Quit gitdashy?', { yes: 'quit', no: 'stay' }))) return
    await post('/api/quit', {})
    setStopped(true)
  }

  const ctx: Ctx = { getData: () => dataRef.current, current, call, setting, flash: setFlash, quit }

  // Launch-time consent questions, one at a time and only when no other dialog is up.
  useEffect(() => {
    if (data?.asks?.length && modalCount() === 0) askConsents(ctx)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.asks])

  const onRefresh = () => call('/api/refresh', {}, 'refreshing…')
  const onAuto = async () => {
    const on = !data?.auto
    let includeExisting = false
    if (on && data?.pending) includeExisting = await confirm(`Auto on. Also review the ${data.pending} already listed?`)
    call('/api/auto', { on, includeExisting }, on ? 'auto on' : 'auto off')
  }
  const onJump = (name: string) => {
    document.querySelector(`[data-fold="${name}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }
  const onPath = (which: 'L' | 'C') => void setPath(ctx, which)
  const onTeams = () => void teamsScreen(ctx, current)
  const onModal = (name: string) => {
    if (name === 'drafts') void draftsScreen(ctx)
    else if (name === 'share') void shareScreen(ctx, current)
    else if (name === 'dream') void dreamScreen(ctx)
    else if (name === 'general') void memoryEditor(ctx, '')
  }
  const onMenu = () => escMenu(ctx)
  const onUpdate = () => void updateScreen(ctx)
  const onContext = useCallback(
    () => setContext((c) => CONTEXTS[(CONTEXTS.indexOf(c) + 1) % CONTEXTS.length]),
    [],
  )

  async function review(p: Row) {
    if (!p || p.busy || p.section !== 'REVIEW REQUESTED') return
    if (tone(p.review)) {
      setFlash(`#${p.number} is already reviewed`)
      return
    }
    if (!(await confirm(`Claude review + post verdict on #${p.number}?`))) return
    await call('/api/review', { url: p.url }, `review started on #${p.number}`)
  }

  async function preReview(p: Row) {
    if (!p || p.busy || p.section !== 'MINE') return
    if (p.pre && !p.pre.moved) {
      const r = await api(`/api/prereview?url=${encodeURIComponent(p.url)}`)
      if (!r.ok) {
        setFlash(`✗ ${await errorText(r)}`)
        return
      }
      const got = await r.json()
      const m = viewer(`pre-review of #${p.number}`, got.text, got.path)
      const copy = () => void call('/api/copy', { text: got.text }, '✓ pre-review copied')
      m.keys!.y = copy
      m.foot!.unshift(['y', 'copy', copy, 'go'])
      repaint()
      return
    }
    if (!(await confirm(p.pre ? `#${p.number} changed since its pre-review. Run again?` : `Pre-review #${p.number}? Nothing is posted.`))) return
    await call('/api/review', { url: p.url, self: true }, `pre-review started on #${p.number}`)
  }

  async function copyUrl(p: Row) {
    if (!p) return
    const out = await call('/api/copy', { url: p.url })
    if (out) setFlash(out.tool === 'terminal' ? `sent ${p.url} to the terminal — if nothing landed, install wl-clipboard or xclip` : `✓ copied ${p.url} (via ${out.tool})`)
  }

  async function addReviewer(p: Row) {
    if (!p || p.section !== 'MINE') return
    setFlash(`fetching collaborators of ${p.repo}…`)
    const r = await api(`/api/collaborators?url=${encodeURIComponent(p.url)}`)
    const logins: string[] = r.ok ? (await r.json()).logins : []
    const ask = async (login: string) => {
      if (login) await call('/api/request-review', { url: p.url, login }, `✓ asked ${login} to review #${p.number}`)
    }
    if (!logins.length) return ask(await prompt(`reviewer login for #${p.number}:`))
    picker(`request review · ${p.repo}#${p.number}`, logins, '', String, ask)
  }

  async function bindScreen(p: Row) {
    const r = await api(`/api/bind?repo=${encodeURIComponent(p.repo)}`)
    if (!r.ok) {
      setFlash(`✗ ${await errorText(r)}`)
      return
    }
    const b = await r.json()
    if (!b.teams.length) {
      setFlash('no teams yet — T starts one')
      return
    }
    const now = (b.kind === 'owner' ? `${b.to}  · via ${b.owner}/*` : b.to) || 'no team'
    picker(
      `bind ${b.repo} — now: ${now}`,
      b.teams.map((t: { key: string }) => t.key),
      b.to,
      (k: string) => b.teams.find((t: { key: string }) => t.key === k)?.name || k,
      (key: string) => void call('/api/bind', { op: 'bind', repo: p.repo, team: key }, `bound to ${key}`),
    )
  }

  function doAct(name: string) {
    if (!current) return
    const p = current
    const fns: Record<string, () => void> = {
      review: () => void review(p),
      pre: () => void preReview(p),
      openpre: () => void call('/api/open', { url: p.url, pre: true }, 'handed the pre-review to the desktop'),
      view: () => {
        if (detail?.review) viewer(`review of #${p.number}`, detail.review.text, `${detail.review.model} ${detail.review.tag}`)
      },
      open: () => void call('/api/open', { url: p.url }),
      copy: () => void copyUrl(p),
      reviewer: () => void addReviewer(p),
      bind: () => void bindScreen(p),
      memory: () => void memoryEditor(ctx, p.repo),
    }
    fns[name]?.()
  }

  function move(step: number) {
    if (!rows.length) return
    const i = rows.findIndex((r) => r.uid === selUid)
    const next = rows[Math.min(rows.length - 1, Math.max(0, i + step))]
    if (next) {
      setSel(next.uid)
      setAt(0)
    }
  }

  function pickSetting(key: string) {
    const s = data?.settings || {}
    const o = data?.options
    if (!o) return
    const on = (name: string, v: unknown) => void setting(name, v)
    if (key === 'm') picker('Model', o.model, s.model || '', String, (v) => on('model', v))
    else if (key === 'd') picker('Depth', o.depth, s.depth || '', String, (v) => on('depth', v))
    else if (key === 'e') picker('Effort', o.effort, s.effort || '', (v) => v || 'default', (v) => on('effort', v))
    else if (key === 's') picker('Summaries', o.subs, s.subs || '', String, (v) => on('subs', v))
    else if (key === 't')
      picker('History', o.window.map((v) => (v == null ? 'all' : String(v))), s.window == null ? 'all' : String(s.window), (v) => (v === 'all' ? 'all' : `${v}h`), (v) => on('window', v === 'all' ? null : +v))
    else if (key === 'i') picker('Refresh', o.interval.map(String), String(s.interval), (v) => every(+v), (v) => on('interval', +v))
    else if (key === 'x') picker('Voices', o.voice, s.voice || [], String, (v) => on('voice', v), true)
    else if (key === 'h') picker('Hunters', o.hunter, s.hunter || [], String, (v) => on('hunter', v), true)
  }

  function handleKey(e: KeyboardEvent) {
    if (modalCount() > 0) return
    const t = e.target as HTMLElement
    if (/input|textarea|select/i.test(t.tagName)) {
      if (e.key === 'Escape') {
        if (t.id === 'q') setQuery('')
        t.blur()
      }
      return
    }
    // ponytail: no chord is bound in this handler, so hand every one back to the host. The ternary
    // this replaces existed to keep ctrl-s out of the single-letter bindings below, and left ctrl-f
    // firing a refresh and eating find. Modals DO bind ctrl+s; that handler keeps its own spelling.
    if (e.ctrlKey || e.metaKey || e.altKey) return
    const k = e.key
    const one = (fn: () => void) => {
      e.preventDefault()
      fn()
    }
    const p = current
    const code = !!(pane && tab === 'code' && p && detail?.url === p.url && detail?.review)
    if (k === 'j' || k === 'ArrowDown') return one(() => move(1))
    if (k === 'k' || k === 'ArrowUp') return one(() => move(-1))
    if (code && ['D', 'n', 'N', 'c'].includes(k))
      return one(() => {
        if (k === 'D') {
          setScope((v) => (v === 'marks' ? 'diff' : 'marks'))
          setDiff(null)
        } else if (k === 'c') {
          onContext()
          setDiff(null)
        } else setAt((v) => v + (k === 'n' ? 1 : -1))
      })
    if (k === 'f') return one(onRefresh)
    if (k === 'a') return one(onAuto)
    if (k === 'D') return one(() => void setting('drafts', !data?.settings.drafts))
    if (k === ' ' && p?.section === 'REVIEWED') return one(() => setExpanded((x) => ({ ...x, [p.url]: !x[p.url] })))
    if ('mdexhsti'.includes(k)) return one(() => pickSetting(k))
    if (k === 'o' && p) return one(() => void call('/api/open', { url: p.url }))
    if (k === '+' && p) return one(() => void addReviewer(p))
    if (k === 'p' && p) return one(() => void preReview(p))
    if (k === 'Y' && p) return one(() => void call('/api/open', { url: p.url, pre: true }, 'handed the pre-review to the desktop'))
    if (k === 'y' && p) return one(() => void copyUrl(p))
    if (k === 'g') return one(() => void memoryEditor(ctx, ''))
    if (k === 'n' && p) return one(() => void memoryEditor(ctx, p.repo))
    if (k === 'Z') return one(() => void dreamScreen(ctx))
    if (k === 'P' && p) return one(() => void shareScreen(ctx, p))
    if (k === 'W') return one(() => void draftsScreen(ctx))
    if (k === 'b' && p) return one(() => void bindScreen(p))
    if (k === '1' || k === '2') return one(() => setTab(k === '1' ? 'summary' : 'code'))
    if (k === 'Tab') return one(() => setTab((v) => (v === 'summary' ? 'code' : 'summary')))
    if (k === 'G') return one(() => setView((v) => (v === 'board' ? 'graph' : 'board')))
    if (k === 'T') return one(() => void teamsScreen(ctx, p))
    if (k === 'L' || k === 'C') return one(() => void setPath(ctx, k))
    if (k === 'u') return one(onUpdate)
    if (k === 'v') return one(() => doAct('view'))
    if (k === 'r' && p) return one(() => void review(p))
    if (k === 'Enter') return one(() => setPane((v) => !v))
    if (k === 'Escape') return one(onMenu)
    if (k === 'q') return one(() => void quit())
    if (k === '/') return one(() => document.getElementById('q')?.focus())
  }
  keyRef.current = handleKey

  if (stopped) return <div className="splash">gitdashy stopped — close this window</div>

  return (
    <div id="app">
      <TopBar data={data} now={now} total={total} onRefresh={onRefresh} onAuto={onAuto} onMenu={onMenu} onUpdate={onUpdate} onLogo={() => setVideo((v) => !v)} view={view} onView={setView} />
      {(data?.notices || []).map((n) => (
        <div className="notice" key={n}>
          {n}
          <span className="x" onClick={() => call('/api/notices')}>
            ✕ dismiss
          </span>
        </div>
      ))}
      <div className="body">
        <Sidebar data={data} secs={secs} onJump={onJump} setting={setting} onPath={onPath} onTeams={onTeams} onModal={onModal} />
        <div className="main">
          <div className="body">
            <div className="queue">
              {view === 'graph' ? (
                <Graph
                  secs={secs}
                  sizes={sizes}
                  measuring={!graph || graph.pending}
                  sel={selUid}
                  onSelect={(uid) => {
                    setSel(uid)
                    setAt(0)
                    setPane(true)
                  }}
                />
              ) : (
              <Queue
                data={data}
                secs={secs}
                now={now}
                sel={selUid}
                read={read}
                query={query}
                onQuery={setQuery}
                failing={failing}
                onFailing={() => setFailing((v) => !v)}
                folded={folded}
                expanded={expanded}
                onFold={(name) => setFolded((f) => ({ ...f, [name]: !f[name] }))}
                onExpand={(u) => setExpanded((e) => ({ ...e, [u]: !e[u] }))}
                onSelect={(uid) => {
                  setSel(uid)
                  setAt(0)
                }}
                onOpen={() => setPane(true)}
              />
              )}
            </div>
            {pane ? (
              <Pane
                p={current}
                detail={detail}
                diff={diff}
                subs={data?.settings.subs || 'all'}
                tab={tab}
                scope={scope}
                context={context}
                at={at}
                onTab={setTab}
                onScope={setScope}
                onContext={onContext}
                onAt={setAt}
                onAct={doAct}
                onClose={() => setPane(false)}
              />
            ) : null}
          </div>
        </div>
      </div>
      {flash ? <div className="toast">{flash}</div> : null}
      {video ? <FloatingVideo /> : null}
      <ModalHost />
    </div>
  )
}