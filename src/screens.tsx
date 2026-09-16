// The screens the curses keys opened, ported from gui.html. Each drives the modal store imperatively
// and mutates its modal in place, then repaint()s — the same shape as the vanilla openModal/paint pair.
import { api, copyText, errorText, post } from './api'
import type { Foot } from './modals'
import { busy, close, confirm, editor, isOpen, notice, open, prompt, repaint, viewer } from './modals'
import type { Ask, Row, StateData } from './types'
import type { LEvent } from './learning'
import { LearningChart } from './components/LearningChart'
import { pageTo } from './board'

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

/** ‹ previous and next › for a screen that shows one item at a time.
 *
 *  ponytail: buttons as well as keys. These screens paged with j and k only, and the footer never said so, so
 *  with a mouse the first item was the only one there was. As footer entries the keys still work -- ModalHost
 *  fires an entry's key -- and they are left out when there is nothing to page to. No Esc entry either: Esc
 *  closes every screen. */
function pager(len: number, go: (step: number) => void): Foot[] {
  return len > 1
    ? [
        ['k', '‹ previous', () => go(-1)],
        ['j', 'next ›', () => go(1)],
      ]
    : []
}

/** Edit one memory file: yours, or a joined team's when `team` is named. Saving a team's file asks first. */
export async function memoryEditor(ctx: Ctx, repo: string, team = '') {
  const r = await api(`/api/memory?repo=${encodeURIComponent(repo || '')}&team=${encodeURIComponent(team)}`)
  if (!r.ok) {
    ctx.flash(`✗ ${await errorText(r)}`)
    return
  }
  const got = await r.json()
  const name = team ? `${team} / ${got.repo}` : got.repo
  editor(
    `memory · ${name}`,
    got.text,
    async (text) => {
      // ponytail: a team's file is every teammate's review memory, and a hand edit skips the two-sightings gate
      // for all of them. Yours costs only you, so it saves as it always did.
      if (team && !(await confirm(`This pushes to team ${team}: every teammate's reviews read ${got.repo === 'general' ? 'its general memory' : `its ${got.repo} memory`}. Save?`, { yes: 'save and push', no: 'keep editing' }))) return false
      const out = await ctx.call('/api/memory', { repo: got.repo, team, text }, `${name} memory saved`)
      if (out?.error) ctx.flash(`saved, but not pushed: ${out.error}`)
    },
    got.path,
  )
}

export type KnowledgeTab = 'learning' | 'waiting' | 'shared'
const TABS: [KnowledgeTab, string][] = [
  ['learning', 'K'],
  ['waiting', 'W'],
  ['shared', 'P'],
]

/** Everything the memory holds, in one panel: how fast it learns, what is waiting for a second sighting, what the
 *  team has of yours, and the general memory and the dream as actions over it.
 *
 *  ponytail: these were five buttons in the rail, each its own screen. One panel with tabs keeps K / W / P as
 *  keys that open it on their tab, and switch tabs inside it. A tab reloads when it is shown, so a dream or an
 *  edit made over the panel is not acted on from a stale list. */
export async function knowledgeScreen(ctx: Ctx, first: KnowledgeTab) {
  const about = ctx.current?.repo || ''
  let tab = first
  let events: LEvent[] = []
  let drafts: Json[] = []
  let promoteAt = 2
  let shared: Json[] = []
  let inTeam = true
  let i = 0
  const failed: Partial<Record<KnowledgeTab, string>> = {}
  let files: { team: string; repo: string }[] = []

  const load = async (t: KnowledgeTab) => {
    const r = await api(t === 'learning' ? '/api/learning' : t === 'waiting' ? '/api/drafts' : `/api/share?about=${encodeURIComponent(about)}`)
    if (!r.ok) {
      failed[t] = await errorText(r)
      return
    }
    delete failed[t]
    const got = await r.json()
    if (t === 'learning') events = got.events
    else if (t === 'waiting') {
      drafts = got.items
      promoteAt = got.promoteAt
    } else {
      shared = got.items
      inTeam = got.inTeam !== false
    }
    if (t === tab) i = pageTo(i, 0, items().length)
  }
  const items = () => (tab === 'waiting' ? drafts : tab === 'shared' ? shared : [])

  const show = async (t: KnowledgeTab) => {
    if (t !== tab) i = 0
    tab = t
    await load(t)
    refresh()
  }
  const go = (step: number) => {
    i = pageTo(i, step, items().length)
    refresh()
  }
  const act = async (path: string, body: Json, ok: string) => {
    await ctx.call(path, body, ok)
    await load(tab)
    refresh()
  }

  const factCard = (repo: unknown, team: string, mark: string, warn: boolean, fact: unknown) => (
    <>
      <div className="kv">
        <span>
          {String(repo || 'general')}
          {team}
        </span>
        <b className={`mark${warn ? ' warn' : ''}`}>{mark}</b>
      </div>
      <div className="fact">{String(fact)}</div>
    </>
  )

  const content = () => {
    if (failed[tab]) return <p className="empty">✗ {failed[tab]}</p>
    if (tab === 'learning') {
      return events.length ? <LearningChart events={events} /> : <p className="empty">nothing learned yet: the chart fills in as reviews propose and confirm facts</p>
    }
    const it = items()[i]
    if (tab === 'waiting') {
      if (!it) return <p className="empty">nothing waiting — every observation so far is either a fact or gone</p>
      const left = promoteAt - (it.n as number)
      const mark = it.kind === 'self' ? 'pre-review · one opinion' : `seen ${it.n}×` + (left > 0 ? ` · ${left} more to go` : ' · confirmed')
      return factCard(it.repo, it.team ? ` · team ${it.team}` : '', mark, true, it.fact)
    }
    if (!it) return <p className="empty">no facts of yours belong to a team yet{inTeam ? '' : ' — you are not in a team'}</p>
    const backers = (it.backers as string[]) || []
    const mark = backers.length > 1 ? `★ ${backers.length} people found this` : it.sent ? 'the team has this' : 'not sent yet'
    return factCard(it.repo, it.team ? ` → ${it.team}` : '', mark, !it.sent, it.fact)
  }

  // every tab up front: the tabs show their counts, and the panel never opens on an empty tab that is still loading
  await Promise.all([
    ...TABS.map(([t]) => load(t)),
    api('/api/memory/files')
      .then((r) => (r.ok ? r.json() : { files: [] }))
      .then((j) => (files = j.files)),
  ])
  const m = open({
    title: 'knowledge',
    wide: true,
    body: () => (
      <div className="kpanel">
        <div className="lbar">
          <div className="seg" role="tablist" aria-label="knowledge">
            {TABS.map(([t, key]) => (
              <button key={t} role="tab" aria-pressed={tab === t} onClick={() => void show(t)}>
                {t}
                {t === 'waiting' && drafts.length ? ` ${drafts.length}` : t === 'shared' && shared.length ? ` ${shared.length}` : ''} <kbd className="hint">{key}</kbd>
              </button>
            ))}
          </div>
          <span className="sp" />
          <select
            value=""
            aria-label="edit a memory file"
            title="edit a memory file: yours, or a team's (saving a team's asks first)"
            onChange={(e) => {
              const [team, repo] = JSON.parse(e.target.value) as [string, string]
              void memoryEditor(ctx, repo, team)
            }}
          >
            <option value="" disabled>
              edit memory…
            </option>
            {[...new Set(files.map((f) => f.team))].map((team) => (
              <optgroup key={team} label={team ? `team ${team}` : 'your memory'}>
                {files
                  .filter((f) => f.team === team)
                  .map((f) => (
                    <option key={f.repo} value={JSON.stringify([f.team, f.repo])}>
                      {team ? `${team} / ${f.repo || 'general'}` : f.repo || 'general'}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
          <div className="tags">
            <button className="tag" onClick={() => void dreamScreen(ctx)}>
              dream <kbd className="hint">Z</kbd>
            </button>
          </div>
        </div>
        {content()}
      </div>
    ),
    foot: [],
  })

  const refresh = () => {
    const list = items()
    const it = list[i]
    m.sub = tab === 'learning' ? (events.length ? `${events.length} events` : '') : `${list.length ? i + 1 : 0}/${list.length}`
    const foot: Foot[] = [...pager(list.length, go)]
    if (tab === 'waiting' && it) {
      foot.push(
        ['t', 'make it a fact', () => void act('/api/drafts', { op: 'promote', repo: it.repo, fact: it.fact }, 'accepted'), 'go'],
        ['x', 'drop', () => void act('/api/drafts', { op: 'drop', repo: it.repo, fact: it.fact }, 'dropped'), 'warn'],
      )
    }
    if (tab === 'waiting' && list.length > 1) foot.push(['s', 'scan for repeats', () => overlapScreen(ctx, () => void show('waiting'))])
    if (tab === 'shared' && it) {
      if (!it.sent) foot.push(['t', 'send it', () => void act('/api/share', { op: 'send', repo: it.repo, fact: it.fact, about }, 'sent'), 'go'])
      foot.push(['x', it.sent ? 'forget it everywhere' : 'forget', () => void act('/api/share', { op: 'forget', repo: it.repo, fact: it.fact, about }, 'forgotten'), 'warn'])
    }
    m.foot = foot
    repaint()
  }
  const step = (by: number) => void show(TABS[pageTo(TABS.findIndex(([t]) => t === tab), by, TABS.length)][0])
  m.keys = {
    K: () => void show('learning'),
    W: () => void show('waiting'),
    P: () => void show('shared'),
    '[': () => step(-1),
    ']': () => step(1),
    g: () => void memoryEditor(ctx, ''),
    Z: () => void dreamScreen(ctx),
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
    // nothing of yours to change: "accept and rewrite memory" offered a rewrite that cannot happen,
    // and the keypress would still take a backup and a commit for it.
    const nothing = (res!.files as Json[]).length === 0
    m.foot = [
      ...(nothing ? [] : [['y', gone.length ? `accept — DELETES ${gone.length} file${gone.length === 1 ? '' : 's'}` : 'accept and rewrite memory', accept, gone.length ? 'warn' : 'go']]),
      ['v', 'view full', () => viewer('the dream', String(res!.detail))],
      ['n', nothing ? 'close — nothing of yours to change' : 'discard', stop],
    ] as Foot[]
    m.keys = { ...Object.fromEntries(m.foot.map((f) => [f[0], f[2]])), Escape: stop }
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

/** The release notes under the header picture: once after an update, and again from the menu. */
export function whatsNew(text: string, version = '') {
  return viewer("what's new", text, `v${version}`, <img className="news" src="/whats-new.webp" alt="" />)
}

export function escMenu(ctx: Ctx) {
  let idx = 0
  // the fourth slot is the board key that does the same thing, where there is one
  const items = (): [string, string, () => void | Promise<void>, string?][] => {
    const s = ctx.getData()?.settings || {}
    return [
      ['Theme', s.theme || 'pencil', () => void cycleTheme(ctx)],
      ['Notify', s.notify ? 'on' : 'off', () => void ctx.setting('notify', !s.notify)],
      ['Refresh', '', async () => { await ctx.call('/api/refresh', {}, 'refreshing…'); close(m) }, 'f'],
      ["What's new", '', async () => {
        const r = await api('/api/changelog')
        if (!r.ok) return ctx.flash(`✗ ${await errorText(r)}`)
        close(m)
        whatsNew((await r.json()).text, ctx.getData()?.version)
      }],
      ['Debug', '', () => { close(m); void debugScreen(ctx) }],
      ['Quit', '', () => void ctx.quit(), 'q'],
    ]
  }
  const m = open({
    title: 'gitdashy',
    body: () =>
      items().map(([l, v, , key], i) => (
        <div key={l} className={`opt${i === idx ? ' on' : ''}`} onClick={() => pick(i)}>
          <span className="tick">{i === idx ? '▸' : ''}</span>
          <span>{l}</span>
          {key ? <kbd className="hint">{key}</kbd> : null}
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
  const copy = async () => ctx.flash(await copyText(text, 'the debug data'))
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
