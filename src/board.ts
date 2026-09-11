// visible()/flat()/selected(), ported from gui.html. Everything here is derived from server data and
// the URL-ish UI state, so nothing needs its own state.
import type { Row, Section, StateData } from './types'
import { rowState, tone } from './tokens'

export type VisSection = Omit<Section, 'prs'> & { prs: Row[] }

export function settings(d: StateData | null) {
  return d?.settings || {}
}

/** Every PR the filters leave, in list order: the drafts rule, the REVIEWED window, the filter box. */
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
        if (failing && tone(p.checks) !== 'changes') return false
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

export function flat(secs: VisSection[], folded: Record<string, boolean>, expanded: Record<string, boolean>): Row[] {
  return secs
    .filter((s) => !folded[s.name])
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
