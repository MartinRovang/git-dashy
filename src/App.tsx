import { useEffect, useMemo, useRef, useState } from 'react'
import { api, errorText, post } from './api'
import { flat, selected, visible } from './board'
import { Pane } from './components/Pane'
import { Queue } from './components/Queue'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { confirm, modalCount, ModalHost, picker, prompt, viewer } from './modals'
import type { Ctx } from './screens'
import { askConsents, draftsScreen, dreamScreen, escMenu, memoryEditor, setPath, shareScreen, teamsScreen, updateScreen } from './screens'
import { CONTEXTS, every, tone } from './tokens'
import type { Code, Detail, Row, StateData } from './types'
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
  const [detail, setDetail] = useState<Detail | null>(null)
  const [diff, setDiff] = useState<Code | null>(null)
  const [tab, setTab] = useState<'summary' | 'code'>('summary')
  const [scope, setScope] = useState('marks')
  const [context, setContext] = useState<number>(CONTEXTS[0])
  const [at, setAt] = useState(0)
  const [stopped, setStopped] = useState(false)

  const secs = useMemo(() => visible(data, query, failing), [data, query, failing])
  const rows = useMemo(() => flat(secs, folded, expanded), [secs, folded, expanded])
  const total = secs.reduce((n, s) => n + s.prs.length, 0)
  const running = data?.running || 0
  const current = selected(rows, sel)
  const selUid = current?.uid || ''
  const url = current?.url || ''

  const dataRef = useRef<StateData | null>(null)
  dataRef.current = data

  useEffect(() => {
    if (!flash) return
    const id = setTimeout(() => setFlash(''), 4000)
    return () => clearTimeout(id)
  }, [flash])

  useEffect(() => {
    document.body.dataset.theme = data?.settings.theme || 'dashy'
  }, [data?.settings.theme])

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
      viewer(`pre-review of #${p.number}`, got.text, got.path)
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

  if (stopped) return <div className="splash">gitdashy stopped — close this window</div>

  return (
    <div id="app">
      <TopBar data={data} now={now} total={total} onRefresh={onRefresh} onAuto={onAuto} onMenu={onMenu} onUpdate={onUpdate} />
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
                onExpand={(u) => setExpanded((e) => ({ ...e, [u]: !e[u] }))}
                onSelect={(uid) => {
                  setSel(uid)
                  setAt(0)
                }}
              />
            </div>
            {pane ? (
              <Pane
                p={current}
                detail={detail}
                diff={diff}
                tab={tab}
                scope={scope}
                context={context}
                at={at}
                onTab={setTab}
                onScope={setScope}
                onContext={() => setContext((c) => CONTEXTS[(CONTEXTS.indexOf(c) + 1) % CONTEXTS.length])}
                onAt={setAt}
                onAct={doAct}
                onClose={() => setPane(false)}
              />
            ) : null}
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
      <ModalHost />
    </div>
  )
}