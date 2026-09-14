// visible()/flat()/selected(), ported from gui.html. Everything here is derived from server data and
// the URL-ish UI state, so nothing needs its own state.
import type { CodeRow, Row, Section, StateData } from './types'
import { rowState, tone } from './tokens'

export type VisSection = Omit<Section, 'prs'> & { prs: Row[] }

export function settings(d: StateData | null) {
  return d?.settings || {}
}

/** Every PR the filters leave, in list order: the drafts rule, the REVIEWED window, the filter box. */
export function visible(d: StateData | null, query: string, failing: boolean, onlyDrafts = false): VisSection[] {
  const q = query.trim().toLowerCase()
  const s = settings(d)
  const cutoff = s.window ? Date.now() - s.window * 3600 * 1000 : 0
  const out: VisSection[] = []
  for (const sec of d?.sections || []) {
    const rows: Row[] = (sec.prs || [])
      .map((p, i) => ({ ...p, uid: `${sec.name}/${i}/${p.url}`, section: sec.name, older: [] }))
      .filter((p) => {
        if (!s.drafts && p.isDraft && !p.busy && !p.review) return false // a draft under review stays visible
        if (sec.name === 'REVIEWED' && cutoff && new Date(p.reviewAt).getTime() < cutoff) return false
        if (failing && tone(p.checks) !== 'changes') return false
        if (onlyDrafts && !p.isDraft) return false
        if (!q) return true
        return `${p.title} ${p.repo} ${p.author} #${p.number}`.toLowerCase().includes(q)
      })
    out.push({ ...sec, prs: sec.name === 'REVIEWED' ? group(rows) : rows })
  }
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

/** The sections one bucket shows. ALL is every section; anything else is the one that matches.
 *
 * ponytail: a bucket that is not in `secs` yields nothing rather than falling back to ALL. The server
 * decides which sections exist, and a saved bucket from a version that had more of them must not
 * silently widen to everything — an empty board says "that queue is gone", a full one says nothing.
 */
export function inBucket(secs: VisSection[], bucket: string): VisSection[] {
  return bucket === ALL ? secs : secs.filter((s) => s.name === bucket)
}

export function flat(secs: VisSection[], bucket: string, expanded: Record<string, boolean>): Row[] {
  return inBucket(secs, bucket)
    .flatMap((s) => s.prs)
    .flatMap((p) => [p, ...(expanded[p.url] ? p.older : [])])
}

export function selected(rows: Row[], sel: string): Row | null {
  return rows.find((p) => p.uid === sel) || rows[0] || null
}

export function counts(d: StateData | null) {
  const all = (d?.sections || []).flatMap((s) => s.prs || []).map(rowState)
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
