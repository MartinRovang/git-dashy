import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, copyText, errorText, post } from './api'
import { ALL, FOLDABLE, NOBODY, type Only, UNFOLDED, buckets, flat, forView, groups, inBucket, isRead, isRefetching, isReviewed, onScreen, pick, pickBucket, pickable, remember, toggleHidden, underScope, visible, walkBucket, whoIs } from './board'
import { FloatingVideo } from './components/FloatingVideo'
import { Graph } from './components/Graph'
import { Necronomicon } from './components/Necronomicon'
import { Shortcuts } from './components/Shortcuts'
import { CodeViewer } from './components/CodeViewer'
import { ReviewTalk } from './components/ReviewTalk'
import { Story } from './components/Story'
import { follow, people, unfollow, type Followed, followAll, FOLLOW_MAX } from './stories'
import { Pane } from './components/Pane'
import { DbGraph } from './components/DbGraph'
import { clean } from './dbgraph'
import { ActsMenu, type Anchor } from './components/Acts'
import { Queue } from './components/Queue'
import { Sidebar } from './components/Sidebar'
import { Countdown, TopBar } from './components/TopBar'
import { close, confirm, findLogin, modalCount, ModalHost, notice, open, picker, prompt, viewer } from './modals'
import type { Foot } from './modals'
import type { Ctx } from './screens'
import { askConsents, draftsScreen, dreamScreen, escMenu, whatsNew, memoryEditor, setPath, shareScreen, teamsScreen, updateScreen } from './screens'
import { CONTEXTS, age, every, span } from './tokens'
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
  const [showHidden, setShowHidden] = useState(false)
  // ponytail: session-only like the rest of the filter row; a setting if people want it to survive a launch
  const [only, setOnly] = useState<Only>(NOBODY)
  // ponytail: the rail shuts to a 106px digest rather than disappearing. A hidden sidebar makes the
  // settings unreachable without remembering a key; a narrow one still answers "which model".
  const [railShut, setRailShut] = useState(false)
  const [help, setHelp] = useState(false)
  /** Ask who, then follow them. The footer button and the rail's View group both call it. */
  const followSomeone = () =>
    void findLogin(
      'Follow someone',
      people((data?.sections || []).flatMap((x) => x.prs)),
      followed.map((f) => f.login),
    ).then((l) => l && setFollowed((f) => follow(f, l)))
  /** Follow everyone the board shows working under one team or org. */
  const followScope = (scope: string) => {
    const who = underScope(data, scope)
    if (!who.length) {
      setFlash(`nobody on the board under ${scope}`)
      return
    }
    // one computation for both, so the flash always describes the list that was written
    const { next, added, left } = followAll(followed, who)
    setFollowed(() => next)
    setFlash(
      left
        ? `following ${added} from ${scope}; ${left} more did not fit, the limit is ${FOLLOW_MAX}`
        : `following ${added} from ${scope}`,
    )
  }
  const [followed, setFollowedState] = useState<Followed[]>([])
  useEffect(() => {
    api('/api/stories')
      .then((r) => (r.ok ? r.json() : { follow: [] }))
      .then((j) => setFollowedState(j.follow))
      .catch(() => {})
  }, [])
  // the post goes here, not inside a state updater: React may run an updater twice. The list the server
  // kept (deduped, checked, capped) replaces ours once it answers, so a pill it will not serve goes away.
  const setFollowed = (f: (l: Followed[]) => Followed[]) => {
    const next = f(followed)
    setFollowedState(next)
    post('/api/stories', { follow: next })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => j && setFollowedState(j.follow))
      .catch(() => {})
  }
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
  // f was pressed: the ticks count that answers it (Infinity until the POST says), and the ⟳ spins till then
  const [pressed, setPressed] = useState<number | null>(null)
  const [view, setView] = useState<'board' | 'graph' | 'necronomicon'>('board')
  // the filter row lives in the queue, so the graph would draw a filtered subset with no way to see
  // or clear it. What gets cleared is forView()'s to say, and tested there; applied in the same
  // update as the switch so the graph lays out once and not twice.
  const show = (v: 'board' | 'graph' | 'necronomicon') => {
    setView(v)
    const f = forView(v, { query, failing, drafts: onlyDrafts, hidden: showHidden, bucket })
    setQuery(f.query)
    setFailing(f.failing)
    setOnlyDrafts(f.drafts)
    setShowHidden(f.hidden)
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

  // same optimistic shape as `marked`: the page's copy wins over the polled one once set
  const [hid, setHid] = useState<Record<string, string> | null>(null)
  const hidden = useMemo(() => hid || data?.settings.hidden || {}, [hid, data])
  // chained, not fired at once: two quick toggles on two connections could land in the wrong order
  const savingHidden = useRef<Promise<unknown>>(Promise.resolve())
  const toggleHide = (p: Row) => {
    const next = toggleHidden(hidden, (data?.sections || []).flatMap((s) => s.prs), p.url)
    setHid(next)
    savingHidden.current = savingHidden.current.then(() =>
      post('/api/settings', { hidden: next })
        .then((r) => (r.ok ? null : errorText(r).then((t) => setFlash(`hidden PRs not saved: ${t}`))))
        .catch(() => setFlash('hidden PRs not saved')),
    )
  }

  const secs = useMemo(() => visible(data, query, failing, onlyDrafts, only, hidden, showHidden), [data, query, failing, onlyDrafts, only, hidden, showHidden])
  const hiddenN = useMemo(
    () => new Set(inBucket(showHidden ? secs : visible(data, query, failing, onlyDrafts, only, hidden, true), bucket).flatMap((s) => s.prs.map((x) => x.url))).size,
    [secs, data, query, failing, onlyDrafts, only, hidden, showHidden, bucket],
  )
  const opts = useMemo(() => whoIs(data), [data])
  const pickOnly = (which: keyof Only) =>
    picker(`only these ${which}`, pickable(opts, only, which), only[which], String, (v) => setOnly((o) => ({ ...o, [which]: v })), true)
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

  // Once after an update: what changed since the version that ran before. Closing it tells the server.
  const changelog = data?.changelog
  useEffect(() => {
    if (!changelog) return
    whatsNew(changelog, dataRef.current?.version).onClose = () => void post('/api/changelog', {})
  }, [changelog])

  useEffect(() => {
    if (!flash) return
    const id = setTimeout(() => setFlash(''), 4000)
    return () => clearTimeout(id)
  }, [flash])

  // only a row you picked counts as read. The fallback selection is a guess — switching tab lands on
  // the top of the new queue, and marking that read is a claim you looked at it.
  useEffect(() => {
    if (current && chosen) markRead([current])
    // markRead is a new function every render: listing it would run this after every render, not on a pick
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        // ponytail: and DROP the inline width the drag was writing. It outranks every stylesheet
        // rule, so once you had resized the rail, `.side.shut` lost to it and collapsing did
        // nothing at all. The var is set on the line above, so the box does not move.
        box.style.width = ''
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

  useEffect(() => {
    if (pressed != null && data && data.ticks >= pressed) setPressed(null)
  }, [data, pressed])
  const onRefresh = () => {
    setPressed(Infinity)
    call('/api/refresh', {}, 'refreshing…').then(
      (r) => setPressed(r ? r.answeredBy : null),
      () => setPressed(null),
    )
  }
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
    if (isReviewed(p)) {
      setFlash(`#${p.number} is already reviewed`)
      return
    }
    if (!(await confirm(`Claude review + post verdict on #${p.number}?`))) return
    await call('/api/review', { url: p.url }, `review started on #${p.number}`)
  }

  async function cast(p: Row, spell: string) {
    if (!p || p.busy || p.section !== 'REVIEW REQUESTED' || isReviewed(p)) return
    if (!(await confirm(`Cast ${spell} on #${p.number}? It runs a review and posts its verdict.`))) return
    await call('/api/review', { url: p.url, spell }, `${spell} cast on #${p.number}`)
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
      // the text copied is what is on screen now, which an accepted revision changes
      let text: string = got.text
      const copy = async () => setFlash(await copyText(text, 'the pre-review'))
      const m = open({
        title: `pre-review of #${p.number}`,
        sub: `${got.path} · never posted`,
        wide: true,
        dismiss: false,
        body: () => (
          <ReviewTalk
            source={{ kind: 'pre', url: p.url }}
            onFlash={setFlash}
            onText={(t) => (text = t)}
            actions={
              <button className="btn" onClick={() => void copy()}>
                <kbd>y</kbd>copy
              </button>
            }
          />
        ),
        foot: [['Esc', 'close', () => close(m)]],
      })
      m.keys = { Escape: () => close(m), y: copy }
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

  /** Review with a message for the agent: what to look at, what to leave alone. Private to this machine. */
  function reviewWith(p: Row) {
    if (!p || p.busy || p.section !== 'REVIEW REQUESTED') return
    if (isReviewed(p)) {
      setFlash(`#${p.number} is already reviewed`)
      return
    }
    const m = open({
      title: `review #${p.number} with instructions`,
      sub: 'kept on this machine; never posted to the PR',
      wide: true,
      dismiss: false,
      focus: '#ask',
      body: () => (
        <textarea
          id="ask"
          placeholder="What should the reviewer focus on, or leave alone? e.g. focus on the migration and the rollback; ignore style."
        />
      ),
      foot: [
        [
          '^S',
          'start the review',
          async () => {
            const ask = (document.querySelector('#ask') as HTMLTextAreaElement).value.trim()
            if (!ask) {
              setFlash('write the instructions first, or press r for a plain review')
              return
            }
            if (await call('/api/review', { url: p.url, ask }, `review started on #${p.number}, with instructions`)) close(m)
          },
          'go',
        ],
      ] as Foot[],
    })
    m.keys = { Escape: () => close(m), 'ctrl+s': () => m.foot![0][2]() }
  }

  /** Inspect a review that is waiting: discuss it with the agent, then post it or drop it. */
  async function waitingScreen(p: Row) {
    const r = await api(`/api/posting?repo=${encodeURIComponent(p.repo)}&number=${p.number}`)
    if (!r.ok) {
      setFlash(`✗ ${await errorText(r)}`)
      return
    }
    const d = await r.json()
    if (!d.held) {
      setFlash('nothing waiting on this PR')
      return
    }
    const post = async () => {
      if (await call('/api/posting', { op: 'release', repo: p.repo, number: p.number }, 'posting…')) close(m)
    }
    const drop = async () => {
      if (await call('/api/posting', { op: 'discard', repo: p.repo, number: p.number }, 'dropped')) close(m)
    }
    const m = open({
      title: `waiting to post — ${p.repo}#${p.number}`,
      sub: `${d.held.model} · nothing is on the PR yet`,
      wide: true,
      // a click beside the screen must not throw away a message half typed; Esc still closes it
      dismiss: false,
      body: () => (
        <ReviewTalk
          source={{ kind: 'held', repo: p.repo, number: p.number }}
          onFlash={setFlash}
          // ponytail: closed only once the post or drop has started. The server refuses while the agent is
          // still answering or a revision waits, and a screen that had already shut left the reason in a
          // flash for a screen you could no longer see.
          actions={
            <>
              <button className="btn warn" onClick={() => void drop()}>
                <kbd>x</kbd>drop it
              </button>
              <button className="btn go" onClick={() => void post()}>
                <kbd>p</kbd>post it
              </button>
            </>
          }
        />
      ),
    })
    m.keys = { Escape: () => close(m), p: () => void post(), x: () => void drop() }
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
      ask: () => reviewWith(p),
      pre: () => void preReview(p),
      view: () => {
        // the detail belongs to the selected PR, so only offer its review for that one
        if (detail?.url === p.url && detail.review)
          viewer(`review of #${p.number}`, detail.review.text, `${detail.review.model} ${detail.review.tag}`)
      },
      db: () => {
        // same rule as view: the detail, and so the db, belongs to the selected PR
        if (detail?.url !== p.url || !detail.review?.db || !clean(detail.review.db).tables.length) return
        const db = detail.review.db
        const m = open({ title: `database of #${p.number}`, sub: p.repo, wide: true, body: () => <DbGraph db={db} number={p.number} />, foot: [['q', 'close', () => close(m)]] })
        m.keys = { Escape: () => close(m) }
      },
      code: () => {
        setSel(p.uid)
        openCode()
      },
      open: () => void call('/api/open', { url: p.url }),
      copy: () => void copyUrl(p),
      reviewer: () => void addReviewer(p),
      bind: () => void bindScreen(p),
      waiting: () => void waitingScreen(p),
      memory: () => void memoryEditor(ctx, p.repo),
      hide: () => toggleHide(p),
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
    // a chosen row only: hiding the fallback guess would take a PR off the board you never looked at
    if (k === 'X' && p && chosen) return one(() => toggleHide(p))
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
    if (k === 'Y' && p?.waiting) return one(() => void waitingScreen(p))
    if ((k === '2' || k === 'Tab') && p) return one(openCode)
    if (k === 'G') return one(() => show(view === 'board' ? 'graph' : view === 'graph' ? 'necronomicon' : 'board'))
    if (k === 'T') return one(() => void teamsScreen(ctx, p))
    if (k === 'L' || k === 'C') return one(() => void setPath(ctx, k))
    if (k === 'u') return one(onUpdate)
    if (k === 'v') return one(() => doAct('view'))
    if (k === 'B') return one(() => doAct('db'))
    if (k === 'r' && p) return one(() => void review(p))
    if (k === 'R' && p) return one(() => reviewWith(p))
    if (k === 'Enter') return one(() => setPane((v) => !v))
    if (k === 'Escape' && help) return one(() => setHelp(false))
    if (k === 'Escape') return one(onMenu)
    if (k === 'q') return one(() => void quit())
    if (k === '/') return one(() => document.getElementById('q')?.focus())
    if (k === '?') return one(() => setHelp((v) => !v))
    if (k === 'F') return one(followSomeone)
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
      <TopBar data={data} spinning={pressed != null} secs={inBucket(secs, bucket)} onRefresh={onRefresh} onAuto={onAuto} onMenu={onMenu} onUpdate={onUpdate} onHelp={() => setHelp((v) => !v)} onLogo={() => setVideo((v) => !v)} view={view} onView={show} only={only} canPick={(w) => pickable(opts, only, w).length > 0} onOnly={pickOnly} onClearOnly={(w) => setOnly((o) => ({ ...o, [w]: [] }))} />
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
          onFollow={followSomeone}
          onFollowScope={followScope}
          followed={followed.length}
          onFollowOwner={(repo) =>
            // both kinds, in turn: `none` takes each rule off, and the owner's word applies again
            void (async () => {
              for (const ran of ['manual', 'auto'] as const) {
                if (!(await call('/api/posting', { repo, ran, post: 'none' }))) return
              }
              setFlash(`${repo} follows ${repo.split('/')[0]}/* again`)
            })()
          }
          onGovern={(owner, on) =>
            // one server operation: the rule for what "one setting for all" means lives in autorev::govern
            void call(
              '/api/posting',
              { op: 'govern', owner, on },
              on ? `${owner}: one setting for every repo` : `${owner}: each repo set on its own`,
            )
          }
          onPosting={(ran, post, target) =>
            void call(
              '/api/posting',
              // an `acme/*` row names the owner; the route takes one or the other, never both
              target.endsWith('/*') ? { owner: target.slice(0, -2), ran, post } : { repo: target, ran, post },
              `${target}: ${ran === 'auto' ? 'auto' : 'your'} reviews ${post === 'hold' ? 'wait' : 'post'}`,
            )
          }
          onAskAgain={onAskAgain}
          onDb={(op, target, db) =>
            void call(
              '/api/dbrepo',
              { op, target, db },
              op === 'clear' ? `${target}: DB repo rule removed` : `${target}: DB repo ${db || 'none'}`,
            )
          }
          onReport={(op) => void call('/api/report', { op }, op === 'start' ? 'writing the Friday report…' : undefined)}
          collapsed={railShut}
          onCollapse={() => setRailShut((v) => !v)}
        />
        <div className="main">
          <div className="body">
            <div className="queue">
              {view === 'necronomicon' ? (
                <Necronomicon selected={current || null} onCast={(p, spell) => void cast(p, spell)} setting={setting} />
              ) : view === 'graph' ? (
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
                only={only}
                onQuery={setQuery}
                failing={failing}
                onFailing={() => setFailing((v) => !v)}
                drafts={onlyDrafts}
                onDrafts={() => setOnlyDrafts((v) => !v)}
                hiddenN={hiddenN}
                showHidden={showHidden}
                onHidden={() => setShowHidden((v) => !v)}
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
        <div className="dock">
          {followed.map((f) => (
            <Story key={f.login} login={f.login} every={data?.interval || 0} onUnfollow={() => setFollowed((l) => unfollow(l, f.login))} />
          ))}
        </div>
        {/* ponytail: here, beside the pills it adds. The control was in the top bar, the whole width of
            the window away from where its result appears. */}
        <button className="lnk foot" title="follow someone: what they are working on, in a footer pill" onClick={followSomeone}>
          + follow
        </button>
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
          hidden={isRead(hidden, menuAt.p)}
          at={menuAt.at}
          onAct={doAct}
          onClose={() => setMenuAt(null)}
        />
      ) : null}
      {flash ? <div className="toast">{flash}</div> : null}
      {video ? <FloatingVideo /> : null}
      <ModalHost />
    </div>
  )
}