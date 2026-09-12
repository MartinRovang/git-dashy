// The screens the curses keys opened, ported from gui.html. Each drives the modal store imperatively
// and mutates its modal in place, then repaint()s — the same shape as the vanilla openModal/paint pair.
import { api, errorText, post } from './api'
import type { Foot } from './modals'
import { busy, close, confirm, editor, isOpen, notice, open, prompt, repaint, viewer } from './modals'
import type { Ask, Row, StateData } from './types'

export type Ctx = {
  getData: () => StateData | null
  current: Row | null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  call: (path: string, body?: unknown, okMsg?: string) => Promise<any>
  setting: (name: string, value: unknown) => Promise<void>
  flash: (msg: string) => void
  quit: () => Promise<void>
}

type Json = Record<string, unknown>

export async function memoryEditor(ctx: Ctx, repo: string) {
  const r = await api(`/api/memory?repo=${encodeURIComponent(repo || '')}`)
  if (!r.ok) {
    ctx.flash(`✗ ${await errorText(r)}`)
    return
  }
  const got = await r.json()
  editor(
    `memory · ${got.repo}`,
    got.text,
    async (text) => {
      const out = await ctx.call('/api/memory', { repo: got.repo, text }, `${got.repo} memory saved`)
      if (out?.error) ctx.flash(`saved, but not pushed: ${out.error}`)
    },
    got.path,
  )
}

export async function draftsScreen(ctx: Ctx) {
  let items: Json[] = []
  let i = 0
  let promoteAt = 2
  const load = async () => {
    const got = await (await api('/api/drafts')).json()
    items = got.items
    promoteAt = got.promoteAt
    i = items.length ? i % items.length : 0
  }
  await load()
  if (!items.length) {
    notice('nothing waiting — every observation so far is either a fact or gone')
    return
  }
  const m = open({
    title: 'waiting',
    body: () => {
      const it = items[i]
      if (!it) return <div>nothing waiting any more</div>
      const left = promoteAt - (it.n as number)
      const mark = it.kind === 'self' ? 'pre-review · one opinion' : `seen ${it.n}×` + (left > 0 ? ` · ${left} more to go` : ' · confirmed')
      return (
        <>
          <div className="kv">
            <span>
              {String(it.repo || 'general')}
              {it.team ? ` · team ${it.team}` : ''}
            </span>
            <b className="mark warn">{mark}</b>
          </div>
          <div className="fact">{String(it.fact)}</div>
        </>
      )
    },
    foot: [
      ['t', 'make it a fact', async () => { const it = items[i]; if (!it) return; await ctx.call('/api/drafts', { op: 'promote', repo: it.repo, fact: it.fact }, 'accepted'); await load(); repaint() }, 'go'],
      ['x', 'drop', async () => { const it = items[i]; if (!it) return; await ctx.call('/api/drafts', { op: 'drop', repo: it.repo, fact: it.fact }, 'dropped'); await load(); repaint() }, 'warn'],
      ['s', 'scan for repeats', () => overlapScreen(ctx, async () => { i = 0; await load(); repaint() })],
      ['Esc', 'close', () => close(m)],
    ] as Foot[],
  })
  const refresh = () => {
    m.sub = `${items.length ? i + 1 : 0}/${items.length}`
    repaint()
  }
  m.keys = {
    j: () => { if (items.length) { i = (i + 1) % items.length; refresh() } },
    k: () => { if (items.length) { i = (i - 1 + items.length) % items.length; refresh() } },
    Escape: () => close(m),
    q: () => close(m),
  }
  refresh()
}

export async function overlapScreen(ctx: Ctx, onDone?: () => void) {
  void ctx
  await post('/api/overlaps', { op: 'start' })
  const model = ctx.getData()?.model || 'the model'
  let pairs: Json[] | null = null
  let i = 0
  const m = open({
    title: 'same fact?',
    dismiss: false,
    body: () => {
      if (pairs === null)
        return (
          <div>
            <span className="spinner" /> {model} is reading the candidates…
          </div>
        )
      if (!pairs.length) return <div>no two drafts look like one fact — nothing to fold</div>
      const p = pairs[i]
      return (
        <>
          <div className="kv">
            <span>{String(p.repo || 'general')}</span>
            <b className={`mark${p.promotes ? '' : ' warn'}`}>
              {String(p.says)} · folds to {String(p.would)}×{p.promotes ? ' · becomes a fact' : ''}
            </b>
          </div>
          <div className="lab" style={{ marginTop: 8 }}>A</div>
          <div className="fact">{String(p.a)}</div>
          <div className="lab">B</div>
          <div className="fact">{String(p.b)}</div>
        </>
      )
    },
    foot: [],
  })
  const done = () => {
    close(m)
    onDone?.()
  }
  const fold = async (keepA: boolean) => {
    const p = pairs![i]
    await ctx.call('/api/overlaps', { op: 'merge', repo: p.repo, keep: keepA ? p.a : p.b, drop: keepA ? p.b : p.a }, 'folded')
    next()
  }
  const next = () => {
    if (!pairs) return
    i += 1
    if (i >= pairs.length) done()
    else repaint()
  }
  m.keys = { Escape: done, q: done }
  const poll = async () => {
    if (!isOpen(m)) return
    const j = await (await api('/api/overlaps')).json()
    if (j.running) {
      repaint()
      setTimeout(poll, 1000)
      return
    }
    if (j.error) {
      close(m)
      notice(`scan failed: ${j.error}`)
      return
    }
    pairs = j.result || []
    m.sub = pairs!.length ? `1/${pairs!.length}` : ''
    if (pairs!.length) {
      m.foot = [
        ['y', 'one fact, keep A', () => fold(true), 'go'],
        ['b', 'keep B', () => fold(false)],
        ['n', 'different', next],
        ['Esc', 'stop', done],
      ] as Foot[]
      m.keys = { ...m.keys, y: () => fold(true), b: () => fold(false), n: next }
    } else {
      m.foot = [['Esc', 'close', done]] as Foot[]
    }
    repaint()
  }
  poll()
}

export async function shareScreen(ctx: Ctx, p: Row | null) {
  const about = p?.repo || ''
  let items: Json[] = []
  let i = 0
  const load = async () => {
    const got = await (await api(`/api/share?about=${encodeURIComponent(about)}`)).json()
    items = got.items
    i = items.length ? i % items.length : 0
    return got
  }
  const got0 = await load()
  if (!items.length) {
    notice(`no facts of yours belong to a team yet${got0.inTeam ? '' : ' — you are not in a team'}`)
    return
  }
  const m = open({
    title: 'the team knows',
    body: () => {
      const it = items[i]
      if (!it) return <div>nothing left to show</div>
      const backers = (it.backers as string[]) || []
      const mark = backers.length > 1 ? `★ ${backers.length} people found this` : it.sent ? 'the team has this' : 'not sent yet'
      return (
        <>
          <div className="kv">
            <span>
              {String(it.repo || 'general')}
              {it.team ? ` → ${it.team}` : ''}
            </span>
            <b className={`mark${it.sent ? '' : ' warn'}`}>{mark}</b>
          </div>
          <div className="fact">{String(it.fact)}</div>
        </>
      )
    },
    foot: [],
  })
  const refresh = () => {
    const it = items[i]
    m.sub = `${items.length ? i + 1 : 0}/${items.length}`
    m.foot = [
      ...(it && !it.sent
        ? ([['t', 'send it', async () => { await ctx.call('/api/share', { op: 'send', repo: it.repo, fact: it.fact, about }, 'sent'); await load(); refresh() }, 'go']] as Foot[])
        : []),
      ...(it
        ? ([['x', it.sent ? 'forget it everywhere' : 'forget', async () => { await ctx.call('/api/share', { op: 'forget', repo: it.repo, fact: it.fact, about }, 'forgotten'); await load(); refresh() }, 'warn']] as Foot[])
        : []),
      ['Esc', 'close', () => close(m)],
    ] as Foot[]
    m.keys = {
      j: () => { if (items.length) { i = (i + 1) % items.length; refresh() } },
      k: () => { if (items.length) { i = (i - 1 + items.length) % items.length; refresh() } },
      Escape: () => close(m),
      q: () => close(m),
    }
    repaint()
  }
  refresh()
}

export async function teamsScreen(ctx: Ctx, p: Row | null) {
  const load = async () => (await api('/api/teams')).json()
  let got = await load()
  const m = open({
    title: got.teams.length ? `teams · ${got.teams.length} joined` : 'teams · none yet',
    body: () =>
      got.teams.length ? (
        got.teams.map((t: Json, i: number) => (
          <div key={String(t.key)} className="opt" onClick={() => pick(i)}>
            <kbd>{i + 1}</kbd>
            <span>{String(t.name)}</span>
            <em>
              {t.arrived ? `${t.arrived} new · ` : ''}
              {String(t.remote || 'no remote yet')}
            </em>
          </div>
        ))
      ) : (
        <div>
          a team is a git repo of shared memory.
          <br />
          start one here, or join one that exists.
        </div>
      ),
    foot: [],
  })
  const pick = async (i: number) => {
    const t = got.teams[i]
    if (t) {
      await teamScreen(ctx, String(t.key), p)
      await reload()
    }
  }
  const refresh = () => {
    m.title = got.teams.length ? `teams · ${got.teams.length} joined` : 'teams · none yet'
    m.foot = [
      ['n', 'start one', newTeam],
      ['a', 'join one', joinTeam],
      ['Esc', 'close', () => close(m)],
    ] as Foot[]
    m.keys = { n: newTeam, a: joinTeam, Escape: () => close(m), q: () => close(m) }
    for (let i = 1; i <= 8; i++) m.keys![String(i)] = () => pick(i - 1)
    repaint()
  }
  const reload = async () => {
    got = await load()
    refresh()
  }
  async function newTeam() {
    const name = await prompt('Name the new team (this is what your repos get bound to):')
    if (!name) return
    const desc = await prompt('One line: what is this team for? (shared with everyone who joins)')
    const owner = p ? p.repo.split('/')[0] : ''
    const cover = owner && (await confirm(`${name.slice(0, 24)} covers ${owner}/*, for everyone who joins?`))
    const out = await busy('starting team', `creating ${name}…`, () =>
      ctx.call('/api/teams', { op: 'new', name, desc, owner: cover ? owner : '' }, `started ${name}`),
    )
    if (out) await teamScreen(ctx, out.key, p)
    await reload()
    await askConsents(ctx)
  }
  async function joinTeam() {
    const repo = await prompt('Existing team (a git URL, owner/name on GitHub, or a path to a bare repo):')
    if (!repo) return
    const out = await busy('joining team', `cloning ${repo}…`, () => ctx.call('/api/teams', { op: 'join', repo }, 'joined'))
    if (out?.warning) await notice(out.warning)
    if (out?.key) await teamScreen(ctx, out.key, p)
    await reload()
    await askConsents(ctx)
  }
  refresh()
}

export function teamScreen(ctx: Ctx, key: string, p: Row | null): Promise<void> {
  return new Promise((res) => {
    const load = async () => (await api('/api/teams')).json().then((g) => g.teams.find((t: Json) => t.key === key))
    void (async () => {
      let t = await load()
      if (!t) return res()
      for (const target of (t.undecided as string[]) || []) {
        const yes = await confirm(`${t.name.slice(0, 24)} now covers ${target} — use it here too?`)
        await ctx.call('/api/teams', { op: 'claim', key, target, yes })
      }
      t = await load()
      const m = open({
        title: `${t.name}  (${key})`,
        body: () => (
          <>
            <div className="kv"><span>what it is for</span><b>{String(t.description || 'nothing yet')}</b></div>
            <div className="kv"><span>checkout</span><b>{String(t.checkout)}</b></div>
            <div className="kv"><span>git remote</span><b>{String(t.remote || 'none yet')}</b></div>
            <div className="kv"><span>used for</span><b>{String(t.used || 'nothing yet')}</b></div>
          </>
        ),
        foot: [],
      })
      const done = () => {
        close(m)
        res()
      }
      const reload = async () => {
        t = await load()
        if (!t) return done()
        repaint()
      }
      const verbs: Record<string, () => Promise<void>> = {
        e: async () => {
          const got = await (await api(`/api/teams?brief=${encodeURIComponent(key)}`)).json()
          editor(
            `brief · ${key}`,
            got.text,
            async (text) => {
              const out = await ctx.call('/api/teams', { op: 'brief', key, text }, 'brief saved')
              if (out?.error) ctx.flash(`saved, but not pushed: ${out.error}`)
            },
            got.path,
          )
        },
        d: async () => {
          const desc = await prompt(`One line: what is ${t.name} for?  [now: ${String(t.description).slice(0, 40) || 'nothing yet'}]`)
          if (desc) {
            await ctx.call('/api/teams', { op: 'describe', key, desc }, 'described')
            await reload()
          }
        },
        c: async () => {
          const url = await prompt(`Git URL for ${key} (an EMPTY repo you can push to — one with history is a team to join):`)
          if (url) {
            const out = await ctx.call('/api/teams', { op: 'connect', key, url })
            if (out) await notice(`${key} now pushes to ${out.remote}`)
            await reload()
          }
        },
        o: async () => {
          const dflt = p ? p.repo.split('/')[0] : ''
          const said = await prompt('Cover which owner?' + (dflt ? ` [${dflt}]` : ' (e.g. neomedsys):'), dflt)
          const owner = said || dflt
          if (!owner) return
          if (!(await confirm(`${t.name.slice(0, 24)} covers ${owner}/*, for everyone who joins?`))) return
          await ctx.call('/api/teams', { op: 'cover', key, owner }, `covers ${owner}/*`)
          await reload()
        },
        x: async () => {
          const what = t.linked ? 'removes only the link, your checkout is kept' : 'DELETES its files'
          if (!(await confirm(`leave ${key}? it ${what} — ${t.checkout}`, { yes: 'leave', no: 'stay' }))) return
          if (await ctx.call('/api/teams', { op: 'leave', key }, `left ${key}`)) done()
        },
      }
      m.foot = [
        ['e', 'brief', verbs.e],
        ['d', 'describe', verbs.d],
        ['c', 'remote', verbs.c],
        ['o', 'cover', verbs.o],
        ['x', 'leave', verbs.x, 'warn'],
        ['Esc', 'back', done],
      ] as Foot[]
      m.keys = { ...verbs, Escape: done, q: done }
      repaint()
    })()
  })
}

export async function dreamScreen(ctx: Ctx) {
  if (!(await confirm('Dream: Claude tidies every memory file (merge, dedupe, drop stale). Nothing is written until you accept. Start?'))) return
  await post('/api/dream', { op: 'start' })
  const model = ctx.getData()?.model || 'the model'
  const SKY = '˖ ⋆ ✧ ✦ ☾ · ° ˚ z Z'.split(' ')
  let seed = 1
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647
  const sky = () => Array.from({ length: 4 }, () => Array.from({ length: 44 }, () => (rnd() < 0.18 ? SKY[Math.floor(rnd() * SKY.length)] : ' ')).join('')).join('\n')
  let res: Json | null = null
  let elapsedS = 0
  const m = open({
    title: 'dreaming',
    dismiss: false,
    body: () =>
      res ? (
        <>
          <pre>{String(res.summary)}</pre>
          <div className="sep" />
          {(res.files as Json[]).map((f, i) => (
            <div key={i} className={`diffrow${f.deleted ? ' gone' : ''}`}>
              <span>{String(f.name)}</span>
              <span>
                {String(f.before)} → {f.deleted ? 'DELETED' : String(f.after)}
              </span>
            </div>
          ))}
          {/* the dream reads the team's files and rewrites none of them: say how many, or one simply
              missing from the list reads as a file it never looked at. */}
          {Number(res.theirs) > 0 && (
            <div className="diffrow">
              <span>
                {String(res.theirs)} team file{Number(res.theirs) === 1 ? '' : 's'} read, none changed
              </span>
              <span />
            </div>
          )}
        </>
      ) : (
        <>
          <div className="sky">{sky()}</div>
          <div style={{ marginTop: 10 }}>
            <span className="spinner" /> {model} is tidying the memories…{' '}
            <span className="mono" style={{ color: 'var(--dim2)' }}>{elapsedS}s</span>
          </div>
        </>
      ),
    foot: [['Esc', 'wake up without changes', () => { close(m); post('/api/dream', { op: 'discard' }) }]] as Foot[],
  })
  const stop = () => {
    close(m)
    post('/api/dream', { op: 'discard' })
  }
  m.keys = { Escape: stop }
  const poll = async () => {
    if (!isOpen(m)) return
    const j = await (await api('/api/dream')).json()
    if (j.running) {
      elapsedS = j.elapsed
      repaint()
      setTimeout(poll, 500)
      return
    }
    if (j.error) {
      close(m)
      notice(`dream failed: ${j.error}`)
      return
    }
    res = j.result
    const gone = (res!.files as Json[]).filter((f) => f.deleted)
    m.title = 'dream over'
    const accept = async () => {
      if (gone.length && !(await confirm(`DELETE ${gone.map((f) => f.name).join(', ')} — ${res!.lost} fact${res!.lost === 1 ? '' : 's'} lost. Sure?`, { yes: 'delete', no: 'keep' }))) return
      const out = await ctx.call('/api/dream', { op: 'apply' }, 'memory rewritten')
      if (out?.error) await notice(out.error)
      close(m)
    }
    // every proposal for a team file means nothing of yours moves, and "accept and rewrite memory"
    // offered a rewrite that cannot happen — the keypress would still take a backup and a commit.
    const nothing = (res!.files as Json[]).length === 0
    m.foot = (nothing
      ? [
          ['v', 'view full', () => viewer('the dream', String(res!.detail))],
          ['n', 'close — nothing of yours to change', stop],
        ]
      : [
          ['y', gone.length ? `accept — DELETES ${gone.length} file${gone.length === 1 ? '' : 's'}` : 'accept and rewrite memory', accept, gone.length ? 'warn' : 'go'],
          ['v', 'view full', () => viewer('the dream', String(res!.detail))],
          ['n', 'discard', stop],
        ]) as Foot[]
    m.keys = nothing
      ? { v: m.foot[0][2], n: stop, Escape: stop }
      : { y: accept, v: m.foot[1][2], n: stop, Escape: stop }
    repaint()
  }
  poll()
}

export async function updateScreen(ctx: Ctx) {
  const v = ctx.getData()?.update
  if (!v) return
  if (!(await confirm(`v${ctx.getData()?.version}  →  v${v}. Installs the release tag and restarts gitdashy. Update now?`, { yes: 'update now', no: 'later' }))) return
  // ponytail: the server reports no progress, so the spinner runs until a failure notice arrives,
  // the process re-execs under a desktop window, or the cap runs out (a --browser page survives the
  // re-exec and would otherwise sit behind it forever). Add a real bar when the server can report one.
  await busy('update', `downloading v${v}…`, async () => {
    const seen = (ctx.getData()?.notices || []).length
    if (!(await ctx.call('/api/update', {}))) return
    for (let i = 0; i < 240; i++) {
      await new Promise((r) => setTimeout(r, 500))
      if ((ctx.getData()?.notices || []).length > seen) return
    }
  })
}

export function escMenu(ctx: Ctx) {
  let idx = 0
  const items = (): [string, string, () => void | Promise<void>][] => {
    const s = ctx.getData()?.settings || {}
    return [
      ['Theme', s.theme || 'pencil', () => void cycleTheme(ctx)],
      ['Notify', s.notify ? 'on' : 'off', () => void ctx.setting('notify', !s.notify)],
      ['Refresh', '', async () => { await ctx.call('/api/refresh', {}, 'refreshing…'); close(m) }],
      ['Debug', '', () => { close(m); void debugScreen(ctx) }],
      ['Quit', '', () => void ctx.quit()],
    ]
  }
  const m = open({
    title: 'gitdashy',
    body: () =>
      items().map(([l, v], i) => (
        <div key={l} className={`opt${i === idx ? ' on' : ''}`} onClick={() => pick(i)}>
          <span className="tick">{i === idx ? '▸' : ''}</span>
          <span>{l}</span>
          <em>{v}</em>
        </div>
      )),
    foot: [['⏎', 'pick', () => pick(idx), 'go'], ['Esc', 'close', () => close(m)], ['q', 'quit', () => void ctx.quit()]] as Foot[],
  })
  const pick = (i: number) => {
    idx = i
    void items()[i][2]()
  }
  const n = () => items().length
  m.keys = {
    j: () => { idx = (idx + 1) % n(); repaint() },
    k: () => { idx = (idx - 1 + n()) % n(); repaint() },
    Enter: () => pick(idx),
    Escape: () => close(m),
    q: () => void ctx.quit(),
  }
}

/** The diagnostic bundle from /api/debug, as pretty JSON you can copy into a bug report. */
export async function debugScreen(ctx: Ctx) {
  const r = await api('/api/debug')
  if (!r.ok) {
    ctx.flash(`✗ ${await errorText(r)}`)
    return
  }
  const text = JSON.stringify(await r.json(), null, 2)
  // the same /api/copy the rest of the app uses, so a headless or wayland box still copies
  const copy = () => void ctx.call('/api/copy', { text }, '✓ debug data copied')
  const m = viewer('debug', text, 'holds repo names, pr urls and config paths — read it before pasting it in public')
  m.keys!.y = copy
  m.foot!.unshift(['y', 'copy', copy, 'go'])
  repaint()
}

async function cycleTheme(ctx: Ctx) {
  const all = ctx.getData()?.options.theme || []
  const cur = ctx.getData()?.settings.theme
  await ctx.setting('theme', all[(all.indexOf(cur || '') + 1) % all.length])
}

export async function setPath(ctx: Ctx, which: 'L' | 'C') {
  const k = ctx.getData()?.knowledge
  const cur = which === 'L' ? k?.memory : k?.store
  const what = which === 'L' ? 'Memory' : 'Store'
  const path = await prompt(`${what} directory${which === 'L' ? ', or a git repo to clone' : ''} [${cur}]:`)
  if (!path) return
  let r = await post('/api/path', { which, path })
  const out = r.ok ? await r.json() : null
  if (!r.ok) {
    ctx.flash(`✗ ${await errorText(r)}`)
    return
  }
  if (out.confirm) {
    if (!(await confirm(out.confirm))) return
    r = await post('/api/path', { which, path, force: true })
    if (!r.ok) {
      ctx.flash(`✗ ${await errorText(r)}`)
      return
    }
  }
  ctx.flash(`${what} is now ${path}`)
}

export async function askConsents(ctx: Ctx, asks?: Ask[]) {
  let list = asks
  if (!list) {
    try {
      list = ((await (await api('/api/asks')).json()).asks as Ask[]) || []
    } catch {
      return // server gone; the next poll retries
    }
  }
  if (!list.length) return
  const a = list[0]
  if (a.kind === 'publishing') {
    const m = open({
      title: `${a.name}  (${a.key})`,
      dismiss: false,
      body: () => (
        <>
          <div>from now on this team receives, for the repos bound to it:</div>
          <div style={{ margin: '8px 0 8px 12px' }}>
            · facts of yours, as they are confirmed
            <br />· what your reviews proposed, unconfirmed
          </div>
          <div className="mono" style={{ color: 'var(--amber)' }}>{a.waiting ? a.waiting + ' waiting to go' : 'nothing waiting yet'}</div>
        </>
      ),
      foot: [['y', 'yes', () => answer(true), 'go'], ['n', 'not this team', () => answer(false)]] as Foot[],
    })
    const answer = async (yes: boolean) => {
      close(m)
      await ctx.call('/api/consent', { kind: 'publishing', key: a.key, yes })
      askConsents(ctx)
    }
    m.keys = { y: () => answer(true), n: () => answer(false) }
  } else {
    const m = open({
      title: `${a.name}  (${a.key})  ·  what it tells your sessions to do`,
      dismiss: false,
      wide: true,
      body: () => (
        <>
          <div>this team's agents.md reaches every session in its repos. it is written by whoever can push to the team's repo.</div>
          <pre style={{ margin: '10px 0', padding: 10, background: 'var(--bg4)', borderRadius: 8, maxHeight: '40vh', overflow: 'auto' }}>{a.text}</pre>
          <div className="mono" style={{ fontSize: 11, color: 'var(--dim2)' }}>{a.path}</div>
          <div style={{ marginTop: 8 }}>reviews never see it. nothing else on this machine changes.</div>
        </>
      ),
      foot: [['y', 'let sessions read it', () => answer(true), 'go'], ['n', 'keep it out', () => answer(false)]] as Foot[],
    })
    const answer = async (yes: boolean) => {
      close(m)
      await ctx.call('/api/consent', { kind: 'agents', key: a.key, yes, text: a.text })
      askConsents(ctx)
    }
    m.keys = { y: () => answer(true), n: () => answer(false) }
  }
}
