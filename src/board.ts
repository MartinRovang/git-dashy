// visible()/flat()/selected(), ported from gui.html. Everything here is derived from server data and
// the URL-ish UI state, so nothing needs its own state.
import type { CodeRow, Row, Section, StateData } from './types'
import { rowState, tone } from './tokens'

export type VisSection = Omit<Section, 'prs'> & { prs: Row[] }

export function settings(d: StateData | null) {
  return d?.settings || {}
}

/** A TEAM row's source is on: its owner's org chip, or the team its repo is bound to. */
function inScope(p: { repo: string; team: string }, scopes: string[]): boolean {
  return scopes.includes(`org:${p.repo.split('/')[0].toLowerCase()}`) || (!!p.team && scopes.includes(`team:${p.team}`))
}

/** Every PR the filters leave, in list order: the drafts rule, the REVIEWED window, the filter box.
 *  TEAM is filtered by the sources toggles here, not by a refetch, and splits in two: rows the logs know a
 *  review of stay TEAM, the rest are OTHER. */
export function visible(d: StateData | null, query: string, failing: boolean): VisSection[] {
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
        if (sec.name === 'TEAM' && (!inScope(p, s.scopes || []) || (cutoff && new Date(p.updatedAt).getTime() < cutoff))) return false
        if (failing && tone(p.checks) !== 'changes') return false
        if (!q) return true
        return `${p.title} ${p.repo} ${p.author} #${p.number}`.toLowerCase().includes(q)
      })
    if (sec.name === 'TEAM') {
      const known = (p: Row) => !!(p.status || p.prev || p.review || p.busy)
      out.push({ ...sec, prs: rows.filter(known) }, { ...sec, name: 'OTHER', prs: rows.filter((p) => !known(p)) })
      continue
    }
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

export function flat(secs: VisSection[], folded: Record<string, boolean>, expanded: Record<string, boolean>): Row[] {
  return secs
    .filter((s) => !folded[s.name])
    .flatMap((s) => s.prs)
    .flatMap((p) => [p, ...(expanded[p.url] ? p.older : [])])
}

/** `hidden` is also searched, so a graph node in a folded section resolves, but the fallback stays a
 *  VISIBLE row: with every section folded it was the first hidden PR, auto-selected and marked read. */
export function selected(rows: Row[], sel: string, hidden: Row[] = []): Row | null {
  return rows.find((p) => p.uid === sel) || hidden.find((p) => p.uid === sel) || rows[0] || null
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
