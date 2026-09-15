import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, copyText, errorText, post } from './api'
import { ALL, buckets, flat, FOLDABLE, forView, groups, inBucket, isRead, isRefetching, onScreen, pick, pickBucket, remember, UNFOLDED, visible, walkBucket } from './board'
import { FloatingVideo } from './components/FloatingVideo'
import { Graph } from './components/Graph'
import { Shortcuts } from './components/Shortcuts'
import { CodeViewer } from './components/CodeViewer'
import { Story } from './components/Story'
import { follow, people, setOpen, unfollow, type Followed } from './stories'
import { Pane } from './components/Pane'
import { ActsMenu, type Anchor } from './components/Acts'
import { Queue } from './components/Queue'
import { Sidebar } from './components/Sidebar'
import { Countdown, TopBar } from './components/TopBar'
import { confirm, findLogin, modalCount, ModalHost, notice, picker, prompt, repaint, viewer } from './modals'
import type { Ctx } from './screens'
import { askConsents, draftsScreen, dreamScreen, escMenu, memoryEditor, setPath, shareScreen, teamsScreen, updateScreen } from './screens'
import { CONTEXTS, age, every, span, tone } from './tokens'
import type { Ask, Code, Detail, Row, StateData } from './types'
import { useStatePoll } from './usePoll'

/** The dashboard: one poll of /api/state, the queue derived from it, and the pane's second request. */
export default function App() {
  const [data, reload] = useStatePoll(2000)
  const [sel, setSel] = useState('')
  const [query, setQuery] = useState('')
  const [failing, setFailing] = useState(false)
  // ponytail: which queues the tabs are on, not which sections are folded. Any number of them stack
  // by click; `[` and `]` walk one at a time and replace the pick.
  const [bucket, setBucket] = useState<string[]>([ALL])
  // ponytail: a FILTER over the bucket, not the `drafts` setting. That setting decides whether drafts
  // are on the board at all; this chip narrows to them, so the two compose — hide drafts and the chip
  // counts zero and goes flat, which is the honest state rather than a contradiction.
  const [onlyDrafts, setOnlyDrafts] = useState(false)
  // ponytail: the rail shuts to a 106px digest rather than disappearing. A hidden sidebar makes the
  // settings unreachable without remembering a key; a narrow one still answers "which model".
  const [railShut, setRailShut] = useState(false)
  const [help, setHelp] = useState(false)
  const [followed, setFollowedState] = useState<Followed[]>([])
  useEffect(() => {
    api('/api/stories')
      .then((r) => (r.ok ? r.json() : { follow: [] }))
      .then((j) => setFollowedState(j.follow))
      .catch(() => {})
  }, [])
  const setFollowed = (f: (l: Followed[]) => Followed[]) =>
    setFollowedState((l) => {
      const next = f(l)
      void post('/api/stories', { follow: next })
      return next
    })
  // the PR the actions popup is about, and where to put it. One state for both the pane's Options
  // button and a right-click on a row.
  const [menuAt, setMenuAt] = useState<{ p: Row; at: Anchor } | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  // ponytail: session-only, like `expanded`; REVIEWED opens folded every launch
  const [unfolded, setUnfolded] = useState<Record<string, boolean>>({})
  const [flash, setFlash] = useState('')
  const [pane, setPane] = useState(true)
  const [video, setVideo] = useState(false)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [diff, setDiff] = useState<Code | null>(null)
  const [codeOpen, setCodeOpen] = useState(false)
  // the viewer floats over a live board: keys go to whichever of the two was clicked last
  const [codeFocus, setCodeFocus] = useState(false)
  const [scope, setScope] = useState('marks')
  const [context, setContext] = useState<number>(CONTEXTS[0])
  const [at, setAt] = useState(0)
  const [stopped, setStopped] = useState(false)
  // the fetchedAt a history change was made on: until a newer fetch lands, the board is the old window
  const [refetchFrom, setRefetchFrom] = useState<number | null>(null)
  const [view, setView] = useState<'board' | 'graph'>('board')
  // the filter row lives in the queue, so the graph would draw a filtered subset with no way to see
  // or clear it. What gets cleared is forView()'s to say, and tested there; applied in the same
  // update as the switch so the graph lays out once and not twice.
  const show = (v: 'board' | 'graph') => {
    setView(v)
    const f = forView(v, { query, failing, drafts: onlyDrafts, bucket })
    setQuery(f.query)
    setFailing(f.failing)
    setOnlyDrafts(f.drafts)
    setBucket(f.bucket)
    // a node picked in the graph may sit in a folded section; open it so the board shows the selection
    const at = current?.section || ''
    if (v === 'board' && chosen && FOLDABLE.includes(at)) setUnfolded((u) => ({ ...u, [at]: true }))
  }
  // url -> the updatedAt that was read, so a PR that moves goes unread again. Saved in the settings
  // file, not localStorage: the GUI picks a new port each launch, so the webview's storage starts empty.
  // `marked` is what this page has read; once set it wins over the polled copy for the page's life.
  const [marked, setMarked] = useState<Record<string, string> | null>(null)
  const read = useMemo(() => marked || data?.settings.read || {}, [marked, data])
  const saveRead = useRef(0)

  const markRead = (prs: Row[]) => {
    if (prs.every((p) => isRead(read, p))) return
    const next = remember(read, prs)
    setMarked(next)
    // debounced: walking the list with j would otherwise rewrite the settings file per row
    clearTimeout(saveRead.current)
    saveRead.current = window.setTimeout(() => {
      post('/api/settings', { read: next })
        .then((r) => (r.ok ? null : errorText(r).then((t) => setFlash(`read marks not saved: ${t}`))))
        .catch(() => setFlash('read marks not saved'))
    }, 500)
  }

  const secs = useMemo(() => visible(data, query, failing, onlyDrafts), [data, query, failing, onlyDrafts])
  // folds are the board's: in the graph a node of a folded section is still clickable, so nothing is folded there
  const rows = useMemo(() => flat(secs, bucket, expanded, view === 'graph' ? UNFOLDED : unfolded), [secs, bucket, expanded, unfolded, view])
  const { row: current, chosen } = pick(rows, sel)
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

  // only a row you picked counts as read. The fallback selection is a guess — switching tab lands on
  // the top of the new queue, and marking that read is a claim you looked at it.
  useEffect(() => {
    if (current && chosen) markRead([current])
  }, [current, chosen])

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
      // document, so the width is written on the dragged box and only copied to :root on release.
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

  // The diff, only while the code viewer is open.
  useEffect(() => {
    if (!url || !codeOpen) return
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
  }, [url, codeOpen, scope, context])

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
          ? span(value as number | null)
          : String(value === '' ? 'default' : value)
    const from = data?.fetchedAt ?? null
    const ok = await call('/api/settings', { [name]: value }, `${name} is now ${shown}`)
    // the server only refetches a new window when a source is on; a failed POST refetches nothing
    if (ok && name === 'window' && data?.settings?.scopes?.length) setRefetchFrom(from)
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
  const onPath = (which: 'L' | 'C') => void setPath(ctx, which)
  const onTeams = () => void teamsScreen(ctx, current)
  // ponytail: forget the recorded answer, then ask. The prompt lists what has NO answer, so re-asking
  // without forgetting first would draw nothing and read as a dead row.
  const onAskAgain = (kind: string, key: string) =>
    void (async () => {
      const out = await ctx.call('/api/consent', { op: 'again', kind, key })
      // ponytail: kind as well as key. launch_asks returns publishing first, so a team with both
      // gates pending opened the publishing dialog from the row that said agents.md.
      if (out?.asks) await askConsents(ctx, (out.asks as Ask[]).filter((a) => a.key === key && a.kind === kind))
    })()
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
      const copy = async () => setFlash(await copyText(got.text, 'the pre-review'))
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
    setFlash(await copyText(p.url, p.url))
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

  function doAct(name: string, target?: Row | null) {
    const p = target || current
    if (!p) return
    const fns: Record<string, () => void> = {
      review: () => void review(p),
      pre: () => void preReview(p),
      view: () => {
        // the detail belongs to the selected PR, so only offer its review for that one
        if (detail?.url === p.url && detail.review)
          viewer(`review of #${p.number}`, detail.review.text, `${detail.review.model} ${detail.review.tag}`)
      },
      code: () => {
        setSel(p.uid)
        openCode()
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
    if (i < 0) return // the selection is not a row on screen: stay put rather than jump to the top
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
      picker('History', o.window.map((v) => (v == null ? 'all' : String(v))), s.window == null ? 'all' : String(s.window), (v) => (v === 'all' ? 'all' : span(+v)), (v) => on('window', v === 'all' ? null : +v))
    else if (key === 'i') picker('Refresh', o.interval.map(String), String(s.interval), (v) => every(+v), (v) => on('interval', +v))
    else if (key === 'x') picker('Voices', o.voice, s.voice || [], String, (v) => on('voice', v), true)
    else if (key === 'h') picker('Hunters', o.hunter, s.hunter || [], String, (v) => on('hunter', v), true)
    else if (key === 'O') picker('Sources', o.scopes, s.scopes || [], String, (v) => on('scopes', v), true)
  }

  function handleKey(e: KeyboardEvent) {
    if (modalCount() > 0) return
    // the actions popup owns the keyboard while it is up; it captures Escape itself so that
    // dismissing it does not also open the app menu
    if (menuAt) return
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
    // while the viewer has focus, board keys do not reach the board; a click on the board hands them back
    if (codeOpen && codeFocus && p) {
      if (k === 'Escape' || k === 'q') return one(() => setCodeOpen(false))
      const last = diff?.url === p.url && !diff.pending ? groups(diff.rows).length - 1 : 0
      if (['j', 'n', 'ArrowDown'].includes(k)) return one(() => setAt((v) => Math.max(0, Math.min(last, v + 1))))
      if (['k', 'N', 'ArrowUp'].includes(k)) return one(() => setAt((v) => Math.max(0, v - 1)))
      if (k === 'D') return one(() => onScope(scope === 'marks' ? 'diff' : 'marks'))
      if (k === 'c') return one(onCodeContext)
      return
    }
    if (k === 'j' || k === 'ArrowDown') return one(() => move(1))
    if (k === 'k' || k === 'ArrowUp') return one(() => move(-1))
    if (k === 'f') return one(onRefresh)
    if (k === 'a') return one(onAuto)
    if (k === 'D') return one(() => void setting('drafts', !data?.settings.drafts))
    if (k === ' ' && p?.section === 'REVIEWED') return one(() => setExpanded((x) => ({ ...x, [p.url]: !x[p.url] })))
    if ('mdexhstiO'.includes(k)) return one(() => pickSetting(k))
    if (k === 'o' && p) return one(() => void call('/api/open', { url: p.url }))
    if (k === '+' && p) return one(() => void addReviewer(p))
    if (k === 'p' && p) return one(() => void preReview(p))
    if (k === 'y' && p) return one(() => void copyUrl(p))
    if (k === 'g') return one(() => void memoryEditor(ctx, ''))
    if (k === 'n' && p) return one(() => void memoryEditor(ctx, p.repo))
    if (k === 'Z') return one(() => void dreamScreen(ctx))
    if (k === 'P' && p) return one(() => void shareScreen(ctx, p))
    if (k === 'W') return one(() => void draftsScreen(ctx))
    if (k === 'b' && p) return one(() => void bindScreen(p))
    if ((k === '2' || k === 'Tab') && p) return one(openCode)
    if (k === 'G') return one(() => show(view === 'board' ? 'graph' : 'board'))
    if (k === 'T') return one(() => void teamsScreen(ctx, p))
    if (k === 'L' || k === 'C') return one(() => void setPath(ctx, k))
    if (k === 'u') return one(onUpdate)
    if (k === 'v') return one(() => doAct('view'))
    if (k === 'r' && p) return one(() => void review(p))
    if (k === 'Enter') return one(() => setPane((v) => !v))
    if (k === 'Escape' && help) return one(() => setHelp(false))
    if (k === 'Escape') return one(onMenu)
    if (k === 'q') return one(() => void quit())
    if (k === '/') return one(() => document.getElementById('q')?.focus())
    if (k === '?') return one(() => setHelp((v) => !v))
    if (k === 'S') return one(() => setRailShut((v) => !v))
    // ponytail: brackets, not 1-5. The digits read better against the tabs, but `2` is a documented
    // binding for the code viewer and it wins whenever a PR is selected, which is nearly always.
    // the tabs are not rendered in graph view, so the keys that move them do nothing there
    if ((k === '[' || k === ']') && view === 'board')
      return one(() => setBucket((cur) => walkBucket(buckets(secs).map((b) => b.key), cur, k === ']' ? 1 : -1)))
  }
  keyRef.current = handleKey

  function openCode() {
    setAt(0)
    setCodeOpen(true)
    setCodeFocus(true)
  }
  // a stale diff of the old scope would flash its files before the new one lands
  function onScope(v: string) {
    setScope(v)
    setAt(0)
    setDiff(null)
  }
  function onCodeContext() {
    onContext()
    setDiff(null)
  }

  const refetching = isRefetching(refetchFrom, data)

  if (stopped) return <div className="splash">gitdashy stopped — close this window</div>

  return (
    <div id="app" className={data?.settings.keyhints === false ? 'hidekeys' : undefined} onPointerDown={(e) => setCodeFocus(!!(e.target as HTMLElement).closest('.cv'))}>
      <TopBar data={data} secs={inBucket(secs, bucket)} onRefresh={onRefresh} onAuto={onAuto} onMenu={onMenu} onUpdate={onUpdate} onHelp={() => setHelp((v) => !v)} onFollow={() => void findLogin('Follow someone', people((data?.sections || []).flatMap((x) => x.prs)), followed.map((f) => f.login)).then((l) => l && setFollowed((f) => follow(f, l)))} onLogo={() => setVideo((v) => !v)} view={view} onView={show} />
      {(data?.notices || []).map((n) => (
        <div className="notice" key={n}>
          {n}
          <span className="x" onClick={() => call('/api/notices')}>
            ✕ dismiss
          </span>
        </div>
      ))}
      <div className="body">
        <Sidebar
          data={data}
          setting={setting}
          onPath={onPath}
          onTeams={onTeams}
          onModal={onModal}
          onAuto={onAuto}
          onAskAgain={onAskAgain}
          collapsed={railShut}
          onCollapse={() => setRailShut((v) => !v)}
        />
        <div className="main">
          <div className="body">
            <div className="queue">
              {view === 'graph' ? (
                <Graph
                  // the whole board: the tabs live in the queue, so a bucket narrowing the graph is a
                  // filter with nothing on screen to see or clear it — the reason show() wipes the rest
                  secs={secs}
                  sel={selUid}
                  read={read}
                  onReadAll={markRead}
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
                sel={selUid}
                read={read}
                onReadAll={() => markRead(onScreen(secs, bucket, unfolded))}
                query={query}
                onQuery={setQuery}
                failing={failing}
                onFailing={() => setFailing((v) => !v)}
                drafts={onlyDrafts}
                onDrafts={() => setOnlyDrafts((v) => !v)}
                bucket={bucket}
                onBucket={(key) => setBucket((cur) => pickBucket(cur, key))}
                expanded={expanded}
                onExpand={(u) => setExpanded((e) => ({ ...e, [u]: !e[u] }))}
                unfolded={unfolded}
                onFold={(name) => setUnfolded((f) => ({ ...f, [name]: !f[name] }))}
                onSelect={(uid) => {
                  setSel(uid)
                  setAt(0)
                }}
                onOpen={() => setPane(true)}
                onMenu={(row, at) => setMenuAt({ p: row, at })}
              />
              )}
            </div>
            {pane ? (
              <Pane
                p={current}
                detail={detail}
                subs={data?.settings.subs || 'all'}
                onCode={openCode}
                onOptions={(at) => current && setMenuAt({ p: current, at })}
                onClose={() => setPane(false)}
              />
            ) : null}
          </div>
        </div>
      </div>
      {codeOpen && current ? (
        <CodeViewer
          p={current}
          c={diff?.url === current.url ? diff : null}
          scope={scope}
          context={context}
          at={at}
          onScope={onScope}
          onContext={onCodeContext}
          onAt={setAt}
          focused={codeFocus}
          onClose={() => setCodeOpen(false)}
        />
      ) : null}
      <footer className="ft">
        <span>j / k move</span>
        <span>⏎ pane</span>
        <span>r review</span>
        <span>? all keys</span>
        <div style={{ flex: 1 }} />
        <div className="sync">
          {refetching ? <span className="spinner" /> : <i style={{ background: data?.error ? 'var(--red)' : 'var(--green)' }} />}
          <span>
            {!data?.fetchedAt
              ? 'fetching…'
              : refetching
                ? 'fetching PRs…'
                : data?.fetching
                  ? 'refreshing…'
                  : <>synced {age(new Date(data.fetchedAt * 1000).toISOString())} ago · next in <Countdown at={data.fetchedAt} interval={data.interval || 0} /></>}
          </span>
        </div>
      </footer>
      {help ? (
        <Shortcuts
          hints={data?.settings.keyhints !== false}
          onHints={() => void setting('keyhints', data?.settings.keyhints === false)}
          onClose={() => setHelp(false)}
        />
      ) : null}
      {menuAt ? (
        <ActsMenu
          p={rows.find((r) => r.uid === menuAt.p.uid) || menuAt.p}
          d={detail?.url === menuAt.p.url ? detail : null}
          at={menuAt.at}
          onAct={doAct}
          onClose={() => setMenuAt(null)}
        />
      ) : null}
      {followed.map((f, i) => (
        <Story key={f.login} f={f} i={i} every={data?.interval || 0} onOpen={(o) => setFollowed((l) => setOpen(l, f.login, o))} onClose={() => setFollowed((l) => unfollow(l, f.login))} />
      ))}
      {flash ? <div className="toast">{flash}</div> : null}
      {video ? <FloatingVideo /> : null}
      <ModalHost />
    </div>
  )
}