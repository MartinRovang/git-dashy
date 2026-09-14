// visible()/flat()/selected(), ported from gui.html. Everything here is derived from server data and
// the URL-ish UI state, so nothing needs its own state.
import type { CodeRow, Row, Section, StateData } from './types'
import { rowState, SECTION_EMPTY, tone } from './tokens'

export type VisSection = Omit<Section, 'prs'> & { prs: Row[] }

export function settings(d: StateData | null) {
  return d?.settings || {}
}

/** A TEAM row's source is on: its owner's org chip, or the team its repo is bound to. */
export function inScope(p: { repo: string; team: string }, scopes: string[]): boolean {
  return scopes.includes(`org:${p.repo.split('/')[0].toLowerCase()}`) || (!!p.team && scopes.includes(`team:${p.team}`))
}

/** Every PR the filters leave, in list order: the drafts rule, the REVIEWED window, the filter box.
 *  TEAM and MERGED are filtered by the sources toggles here, not by a refetch. TEAM splits in two: rows
 *  the logs know a review of stay TEAM, the rest are OTHER. MERGED goes last. */
export function visible(d: StateData | null, query: string, failing: boolean, onlyDrafts: boolean): VisSection[] {
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

export type Filters = { query: string; failing: boolean; drafts: boolean; bucket: string[] }

/** The filter row after a view switch: cleared for the graph, untouched for the board.
 *
 * ponytail: the graph has no filter row of its own, so a narrowed board there is a filter you can
 * neither see nor clear — and a node outside the pick resolves to a uid `flat()` never produced,
 * which `selected()` then answers with rows[0]. It lives here because `show()` has no harness and
 * this is the third field that has been forgotten in it.
 */
export function forView(v: 'board' | 'graph', cur: Filters): Filters {
  return v === 'graph' ? { query: '', failing: false, drafts: false, bucket: [ALL] } : cur
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
export function emptyLine(d: StateData | null, name: string, query: string, failing: boolean, drafts: boolean): string {
  if (query.trim() || failing || drafts) return 'Nothing matches the filter.'
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

/** The read map with `prs` marked at their current updatedAt, keeping only the `keep` newest.
 *
 * ponytail: pruned by age, not by what is on the board, since a narrower window or a scope turned off
 * would forget marks for PRs that come back. The server caps at 5000.
 */
export function remember(read: Record<string, string>, prs: Pick<Row, 'url' | 'updatedAt'>[], keep = 2000): Record<string, string> {
  const all = { ...read, ...Object.fromEntries(prs.map((p) => [p.url, p.updatedAt])) }
  return Object.fromEntries(Object.entries(all).sort((a, b) => b[1].localeCompare(a[1])).slice(0, keep))
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
