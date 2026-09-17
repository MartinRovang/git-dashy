// visible()/flat()/selected(), ported from gui.html. Everything here is derived from server data and
// the URL-ish UI state, so nothing needs its own state.
import type { CodeRow, PostingRule, Pr, Row, Section, StateData, Talk } from './types'
import { rowState, SECTION_EMPTY, tone } from './tokens'
import { people } from './stories'

export type VisSection = Omit<Section, 'prs'> & { prs: Row[] }

export function settings(d: StateData | null) {
  return d?.settings || {}
}

/** A TEAM row's source is on: its owner's org chip, or the team its repo is bound to. */
export function inScope(p: { repo: string; team: string }, scopes: string[]): boolean {
  return scopes.includes(`org:${p.repo.split('/')[0].toLowerCase()}`) || (!!p.team && scopes.includes(`team:${p.team}`))
}

/** Everyone the board shows under one `team:x` or `org:y` chip, ready to follow: authors and reviewers of
 *  its rows, deduped by `people`, in its order. Rows the sources toggles have hidden are still in `d`, so
 *  this is every PR the board holds under that scope, not only the visible ones. */
export function underScope(d: StateData | null, scope: string): string[] {
  const secs = d?.sections || []
  const prs = secs.flatMap((s) => s.prs).filter((p): p is Pr => !!p)
  // ponytail: not you. MINE is the search for your own PRs, so its authors are you, and following yourself
  // under your own org put a pill of your own work in your footer.
  const me = new Set(
    secs
      .filter((s) => s.name === 'MINE')
      .flatMap((s) => s.prs)
      .filter((p): p is Pr => !!p)
      .map((p) => p.author.toLowerCase()),
  )
  return people(prs.filter((p) => inScope(p, [scope]))).filter((l) => !me.has(l.toLowerCase()))
}

/** Which rule decides one axis of one posting row, as three states rather than two appearances. `via` from
 *  the server says where the word came from; what makes it readable is whose row it is on:
 *
 *  - `own` — this target's own rule. An `acme/*` row's owner rule, or an `acme/api` row's repo rule.
 *  - `owner` — no rule here; the word comes from the owner above. Only a repo row can be this.
 *  - `none` — nobody set anything and the word is the default.
 *
 *  ponytail: without the third, "no arrow" meant either "set here" or "nothing set", which are opposite
 *  answers to the only question this panel exists to answer.
 */
export function ruleSource(target: string, via: '' | 'repo' | 'owner'): 'own' | 'owner' | 'none' {
  if (!via) return 'none'
  return via === (target.endsWith('/*') ? 'owner' : 'repo') ? 'own' : 'owner'
}

/** Whether a posting row carries a rule of its own on either axis, as opposed to following or defaulting. */
export const hasOwnRule = (r: PostingRule) =>
  ruleSource(r.target, r.manualVia) === 'own' || ruleSource(r.target, r.autoVia) === 'own'

export type PostingNode = {
  owner: PostingRule
  /** Every repo under it, in the order the server listed them. */
  repos: PostingRule[]
  /** The owner carries a rule, so it is what decides for every repo below that has none of its own. */
  governs: boolean
  /** Under a governing owner, the repos that still have a rule of their own -- which beats the owner's.
   *
   *  ponytail: kept apart so the panel can show them. Switching the owner on takes every repo rule off,
   *  but a store written before the switch existed (the old `H` screen, or the CLI) can hold one, and a
   *  panel that only said "all repos: you hold" drew nothing for the repo that posts anyway. Empty when
   *  the owner does not govern: then every repo is set on its own and none is an exception. */
  exceptions: PostingRule[]
}

/** The posting panel as a tree: an owner, then what it owns.
 *
 *  ponytail: a tree, not a list with markers on it. Flat rows meant "acme/*" and the three repos under it
 *  each stated the same rule with a different decoration, and which one was in charge had to be worked out
 *  from an arrow. An owner is a parent here, and its repos are drawn inside it.
 *
 *  When the owner `governs`, the repos below have nothing to set: the owner decides, so they carry no
 *  controls. The exception is a repo the store already has a rule for — that one beats the owner, so it
 *  keeps its controls. Hiding it would leave a rule in force with nothing on screen able to reach it.
 */
export function postingTree(rules: PostingRule[]): PostingNode[] {
  const nodes = new Map<string, PostingNode>()
  const node = (name: string) => {
    const at = nodes.get(name)
    if (at) return at
    // a repo whose owner row the server did not send still gets a parent, so no repo is ever dropped
    const made: PostingNode = {
      owner: { target: `${name}/*`, manual: 'post', auto: 'post', manualVia: '', autoVia: '' },
      repos: [],
      governs: false,
      exceptions: [],
    }
    nodes.set(name, made)
    return made
  }
  for (const r of rules) {
    const name = r.target.split('/')[0]
    if (r.target.endsWith('/*')) {
      node(name).owner = r
      // a per-repo owner may still carry a rule -- the fallback for repos nobody listed -- and that is not "decides"
      node(name).governs = hasOwnRule(r) && !r.perRepo
    } else {
      node(name).repos.push(r)
    }
  }
  for (const n of nodes.values()) n.exceptions = n.governs ? n.repos.filter(hasOwnRule) : []
  return [...nodes.values()]
}

/** Every PR the filters leave, in list order: the drafts rule, the REVIEWED window, the filter box.
 *  TEAM and MERGED are filtered by the sources toggles here, not by a refetch. TEAM splits in two: rows
 *  the logs know a review of stay TEAM, the rest are OTHER. MERGED goes last. Hidden PRs are left out,
 *  or with `showHidden` they are all that is left. */
export function visible(
  d: StateData | null,
  query: string,
  failing: boolean,
  onlyDrafts: boolean,
  only: Only,
  hidden: Record<string, string> = {},
  showHidden = false,
): VisSection[] {
  const q = query.trim().toLowerCase()
  const s = settings(d)
  const cutoff = s.window ? Date.now() - s.window * 3600 * 1000 : 0
  const out: VisSection[] = []
  let other: VisSection | null = null // last, below REVIEWED
  let merged: VisSection | null = null // below OTHER: the bottom of the board
  for (const sec of d?.sections || []) {
    const sourced = sec.name === 'TEAM' || sec.name === 'MERGED'
    // every source toggled off: the server still searches them this session, but there is nothing to show
    if (sourced && !s.scopes?.length) continue
    const rows: Row[] = (sec.prs || [])
      .map((p, i) => ({ ...p, uid: `${sec.name}/${i}/${p.url}`, section: sec.name, older: [] }))
      .filter((p) => {
        if (!s.drafts && p.isDraft && !p.busy && !p.review) return false // a draft under review stays visible
        if (sec.name === 'REVIEWED' && cutoff && new Date(p.reviewAt).getTime() < cutoff) return false
        if (sourced && !inScope(p, s.scopes || [])) return false // the window is in the search itself
        if (failing && tone(p.checks) !== 'changes') return false
        if (onlyDrafts && !p.isDraft) return false
        if (isRead(hidden, p) !== showHidden) return false // same rule as read: a PR that moves comes back
        if (only.repos.length && !only.repos.includes(p.repo)) return false
        if (only.authors.length && !only.authors.includes(p.author)) return false
        if (!q) return true
        return `${p.title} ${p.repo} ${p.author} #${p.number}`.toLowerCase().includes(q)
      })
    if (sec.name === 'TEAM') {
      const known = (p: Row) => !!(p.status || p.prev || p.review || p.busy)
      out.push({ ...sec, prs: rows.filter(known) })
      other = { ...sec, name: 'OTHER', error: '', prs: rows.filter((p) => !known(p)).map((p) => ({ ...p, section: 'OTHER', uid: p.uid.replace(/^TEAM\//, 'OTHER/') })) }
      continue
    }
    if (sec.name === 'MERGED') {
      merged = { ...sec, prs: rows }
      continue
    }
    out.push({ ...sec, prs: sec.name === 'REVIEWED' ? group(rows) : rows })
  }
  if (other?.prs.length) out.push(other)
  if (merged) out.push(merged)
  return out
}

/** The repo and author picks. An empty list is every one of them, not none.
 *
 * ponytail: kept apart from `Filters` on purpose — forView() wipes those for the graph because the
 * graph cannot show them, but these sit in the top bar, which both views have.
 */
export type Only = { repos: string[]; authors: string[] }
export const NOBODY: Only = { repos: [], authors: [] }

/** What the repo and author pickers can offer. Same shape as `Only`, but these are the choices, not the pick. */
export type Options = { repos: string[]; authors: string[] }

/** Every repo and author on the raw board, sorted, so a pick never hides the options to undo it. */
export function whoIs(d: StateData | null): Options {
  const prs = (d?.sections || []).flatMap((s) => s.prs || [])
  const uniq = (xs: string[]) => [...new Set(xs.filter(Boolean))].sort((a, b) => a.localeCompare(b))
  return { repos: uniq(prs.map((p) => p.repo)), authors: uniq(prs.map((p) => p.author)) }
}

/** One picker's list: the board's options, then any pick that left the board (merged away, window
 *  narrowed) so it can still be unticked. picker(many) drops a ticked value that is not in its list. */
export function pickable(opts: Options, only: Only, which: keyof Only): string[] {
  return [...opts[which], ...only[which].filter((v) => !opts[which].includes(v))]
}

/** REVIEWED lists one entry per REVIEW; the newest keeps the row and the rest fold under it. */
function group(rows: Row[]): Row[] {
  const by = new Map<string, Row>()
  for (const p of rows) {
    const head = by.get(p.url)
    if (head) head.older.push(p)
    else by.set(p.url, { ...p, older: [] })
  }
  return [...by.values()]
}

/** The bucket tabs: one per section the server sent, with All in front. */
export const ALL = 'ALL'

export function buckets(secs: VisSection[]): { key: string; label: string; n: number }[] {
  return [
    { key: ALL, label: 'All', n: secs.reduce((t, s) => t + s.prs.length, 0) },
    ...secs.map((s) => ({ key: s.name, label: s.name.toLowerCase(), n: s.prs.length })),
  ]
}

/** The sections the picked buckets show. More than one tab can be on at a time; ALL is every
 *  section, and an empty pick is none.
 *
 * ponytail: a bucket that is not in `secs` contributes nothing rather than widening to ALL. The
 * server decides which sections exist, and a saved pick from a version that had more of them must
 * not silently show everything — an empty board shows the queue is gone. An empty pick is the same:
 * nothing named, nothing shown. `pickBucket` never produces one, and the tab strip reads it the same
 * way, so the two cannot disagree.
 * Order follows `secs`, never the order they were clicked, so the board does not reshuffle.
 */
export function inBucket(secs: VisSection[], bucket: readonly string[]): VisSection[] {
  if (bucket.includes(ALL)) return secs
  return secs.filter((s) => bucket.includes(s.name))
}

/** Click a tab: All replaces the pick, a section toggles into it, and emptying it falls back to All. */
export function pickBucket(bucket: readonly string[], key: string): string[] {
  if (key === ALL) return [ALL]
  const rest = bucket.filter((b) => b !== ALL && b !== key)
  return bucket.includes(key) && !bucket.includes(ALL) ? (rest.length ? rest : [ALL]) : [...rest, key]
}

export type Filters = { query: string; failing: boolean; drafts: boolean; hidden: boolean; bucket: string[] }

/** The filter row after a view switch: cleared for the graph, and the board's own row (`kept`, saved
 *  as you left it) back on the board, so a trip through the graph or the book does not cost your tabs.
 *
 * ponytail: the graph has no filter row of its own, so a narrowed board there is a filter you can
 * neither see nor clear — and a node outside the pick resolves to a uid `flat()` never produced,
 * which `selected()` then answers with rows[0]. It lives here because `show()` has no harness and
 * this is the third field that has been forgotten in it.
 */
export function forView(v: 'board' | 'graph' | 'necronomicon', cur: Filters, kept: Filters): Filters {
  if (v === 'graph') return { query: '', failing: false, drafts: false, hidden: false, bucket: [ALL] }
  return v === 'board' ? kept : cur
}

/** The two filter chips over the bucket on screen, with the rule for when one goes dead.
 *
 * ponytail: counted over the BUCKET, not the whole board — a chip offering "3 failing" while you are
 * looking at a queue holding none of them is a number you cannot act on. `secs` already has the other
 * chip's filter applied, so the number is how many rows pressing THIS one would leave: a zero means
 * the pair is empty, and a dead chip is the honest answer.
 */
export function chips(
  secs: VisSection[],
  bucket: readonly string[],
  failing: boolean,
  drafts: boolean,
): { key: 'failing' | 'drafts'; label: string; n: number; on: boolean; off: boolean }[] {
  const shown = inBucket(secs, bucket).flatMap((s) => s.prs)
  return [
    { key: 'failing' as const, label: 'CI failing', n: shown.filter((x) => tone(x.checks) === 'changes').length, on: failing },
    { key: 'drafts' as const, label: 'Drafts', n: shown.filter((x) => x.isDraft).length, on: drafts },
  ].map((c) => ({ ...c, off: !c.n && !c.on }))
}

/// `[` and `]`: one tab at a time, replacing the pick.
export function walkBucket(keys: string[], cur: readonly string[], dir: 1 | -1): string[] {
  if (!keys.length) return [...cur]
  // a stacked pick has no single place in the strip, so the walk starts from All
  const i = keys.indexOf(cur.length === 1 ? cur[0] : ALL)
  return [keys[((i < 0 ? 0 : i) + dir + keys.length) % keys.length]]
}

/** What an empty section says.
 *
 * ponytail: the per-queue line ("Nothing of yours is open.") is a claim about the queue, and a
 * filter emptying the section makes it false — type `/foo` and MINE says you have nothing open. The
 * queue line is only true when nothing is narrowing the board.
 */
export function emptyLine(d: StateData | null, name: string, query: string, failing: boolean, drafts: boolean, only: Only): string {
  if (query.trim() || failing || drafts || only.repos.length || only.authors.length) return 'Nothing matches the filter.'
  const win = settings(d).window
  if (name === 'REVIEWED' && win) return `Nothing reviewed in the last ${win}h.`
  return SECTION_EMPTY[name] || 'Nothing here.'
}

/** REVIEWED, OTHER and MERGED start folded while they share the board with other queues; their own tab always shows them.
 *  `unfolded` is what the header clicks opened. */
export const FOLDABLE = ['REVIEWED', 'OTHER', 'MERGED']
/** Every foldable section open: the graph draws them all, so its clicks must resolve against them all. */
export const UNFOLDED = Object.fromEntries(FOLDABLE.map((n) => [n, true]))

export function folded(name: string, shown: number, unfolded: Record<string, boolean>): boolean {
  return FOLDABLE.includes(name) && shown > 1 && !unfolded[name]
}

/** The PRs actually on screen: the picked buckets minus folded sections. Unread counts and "Read all"
 *  go through here too, so a fold never marks read what it hides. */
export function onScreen(secs: VisSection[], bucket: readonly string[], unfolded: Record<string, boolean> = {}): Row[] {
  const shown = inBucket(secs, bucket)
  return shown.filter((s) => !folded(s.name, shown.length, unfolded)).flatMap((s) => s.prs)
}

export function flat(
  secs: VisSection[],
  bucket: readonly string[],
  expanded: Record<string, boolean>,
  unfolded: Record<string, boolean> = {},
): Row[] {
  return onScreen(secs, bucket, unfolded).flatMap((p) => [p, ...(expanded[p.url] ? p.older : [])])
}

/** Read when the mark is at or past the row's updatedAt; a PR that moves past its mark is unread again.
 *  Compared as times, not strings: REVIEWED rows carry the log's `+00:00` and GitHub rows `Z`. A time that
 *  does not parse (an empty updatedAt) falls back to the string. */
export function isRead(read: Record<string, string>, p: Pick<Row, 'url' | 'updatedAt'>): boolean {
  const at = read[p.url]
  if (at === undefined) return false
  const [a, b] = [Date.parse(at), Date.parse(p.updatedAt)]
  return Number.isNaN(a) || Number.isNaN(b) ? at >= p.updatedAt : a >= b
}

/** The read map with `prs` marked at their current updatedAt, keeping only the `keep` newest.
 *
 * ponytail: pruned by age, not by what is on the board, since a narrower window or a scope turned off
 * would forget marks for PRs that come back. The server caps at 5000.
 */
export function remember(read: Record<string, string>, prs: Pick<Row, 'url' | 'updatedAt'>[], keep = 2000): Record<string, string> {
  const all = { ...read }
  // forward only: one url sits in two sections with two times (a MERGED row carries GitHub's, its REVIEWED
  // row the log's), and marking one must not rewind the other to unread
  for (const p of prs) if (!isRead(all, p)) all[p.url] = p.updatedAt
  return Object.fromEntries(Object.entries(all).sort((a, b) => b[1].localeCompare(a[1])).slice(0, keep))
}

/** The hidden map with `url` unhidden if it is hidden, or hidden at the newest time of `rows` — every
 *  row the board has for that url, so a PR in MERGED and REVIEWED goes out of both. */
export function toggleHidden(hidden: Record<string, string>, rows: Pick<Row, 'url' | 'updatedAt'>[], url: string): Record<string, string> {
  const mine = rows.filter((r) => r.url === url)
  if (mine.some((r) => isRead(hidden, r))) return Object.fromEntries(Object.entries(hidden).filter(([u]) => u !== url))
  return remember(hidden, mine)
}

/** The selected row, and whether it is the one that was actually chosen.
 *
 * ponytail: the fallback exists so the board is never without a selection, but it is a guess, not a
 * choice — and marking it read is a claim you looked at it. Switching tabs lands on rows[0] of the
 * new queue, so every `[` and `]` step was marking the top PR of that queue read.
 */
export function pick(rows: Row[], sel: string): { row: Row | null; chosen: boolean } {
  const found = rows.find((p) => p.uid === sel)
  return { row: found || rows[0] || null, chosen: !!found }
}

/** Whether this PR already has a verdict the author has seen.
 *
 * ponytail: the ONE place that asks. The hold path writes its verdict into `review` so the row can
 * say "waiting to post", and `tone()` matches it — so every caller that forgot `!waiting` treated a
 * held review as a posted one. It was got right in the row state and in the actions menu and missed
 * in the `r` handler, which is two out of three by habit and one bug.
 */
export function isReviewed(p: Pr): boolean {
  return !p.waiting && !!tone(p.review)
}

/** A spell looks at one topic and posts nothing, so it runs on any PR nothing else is running on, except a
 *  team's memory repo: a spell is a model review, and those are approved by a person. */
export function canCastOn(p: Row): boolean {
  return !p.busy && !p.humanOnly
}

/** A spell cast from this app, waiting for its row to finish. `busy` is whether a poll has shown the row running. */
export type Cast = { spell: string; since: number; busy: boolean }

/** Where a cast stands on this poll: `gone` when its PR left the board, `ended` once the row was seen busy and
 *  is not any more, else `wait`. Marks the cast busy when the row is.
 *
 *  ponytail: a cast that fails inside one poll is never seen busy and waits until its row next runs or leaves. */
export function castStep(c: Cast, row: Pr | undefined): 'wait' | 'gone' | 'ended' {
  if (!row) return 'gone'
  if (row.busy) c.busy = true
  return c.busy && !row.busy ? 'ended' : 'wait'
}

/** This cast's result among the PR's spells. An older result of the same spell is not it; a second of slack for
 *  the file's mtime against the page's clock. */
export function castResult<S extends { name: string; at: number }>(spells: S[], c: Cast): S | undefined {
  return spells.find((s) => s.name === c.spell && s.at >= c.since - 1)
}

export function selected(rows: Row[], sel: string): Row | null {
  return pick(rows, sel).row
}

export function counts(d: StateData | null) {
  // TEAM and MERGED verdicts come from reviews REVIEWED already counts
  const all = (d?.sections || []).filter((s) => s.name !== 'TEAM' && s.name !== 'MERGED').flatMap((s) => s.prs || []).map(rowState)
  const by = (t: string) => all.filter((r) => r.key === t).length
  return [
    ['approved', by('approved'), 'var(--green)'],
    ['changes', by('changes'), 'var(--red)'],
    ['commented', by('commented'), 'var(--amber)'],
    ['errors', by('error'), 'var(--red)'],
  ] as const
}

type Group = { label: string; add?: number; dele?: number; marks: number; rows: CodeRow[] }

/** The diff cut at its file rows. Orphans (marks that found no line) close it as their own group. */
export function groups(rows: CodeRow[]): Group[] {
  const files: Group[] = []
  const loose: Group = { label: 'not on a diff line', marks: 0, rows: [] }
  for (const r of rows) {
    if (r.kind === 'orphan') {
      loose.rows.push(r)
      loose.marks++
      continue
    }
    if (r.kind === 'file') files.push({ label: r.path, add: r.add, dele: r.dele, marks: 0, rows: [] })
    const g = files[files.length - 1]
    if (!g) continue
    if (r.kind !== 'file') g.rows.push(r)
    if (r.kind === 'line' && r.mark) g.marks++
  }
  return loose.rows.length ? [...files, loose] : files
}

/** The footer's "fetching PRs…": a fetch is running and none has landed since the history change.
 *
 * ponytail: a tick already running when the window changes fetches the OLD window, and this shows for
 * it; when it lands the spinner goes, and the real refetch follows up to WAKE_GAP later as "refreshing…".
 * A settings reply saying which tick carries the new window is the upgrade if that gap is felt.
 */
export function isRefetching(from: number | null, d?: Pick<StateData, 'fetching' | 'fetchedAt'> | null): boolean {
  return from != null && !!d?.fetching && d.fetchedAt === from
}

/** What the review screen lets you press, from the saved conversation and what is typed.
 *
 *  ponytail: here, not in the component, which has no render harness. Every rule below is one the server
 *  also enforces -- this only keeps a button from offering what would be refused. `decide` is accept or keep:
 *  only with a revision waiting, and never while the agent is still working. */
export function talkControls(t: Talk | null, draft: string) {
  const open = !!t && !t.cannotDiscuss
  const idle = !!t && !t.busy
  return {
    type: open && idle,
    send: open && idle && draft.trim() !== '',
    revise: open && idle && !t.proposed && t.thread.length > 0,
    decide: idle && !!t.proposed,
  }
}

/** The item one `step` along from `i` in a list of `len`, wrapping at both ends; 0 for an empty list. */
export const pageTo = (i: number, step: number, len: number) => (len ? (((i + step) % len) + len) % len : 0)
