// The shape of the pane's database graph (components/DbGraph.tsx): the PR in the middle, the tables it touches
// around it, the columns it changes hanging off each table, and foreign keys between tables.
//
// Pure: the nodes and links only. components/DbGraph.tsx runs them live, the way the board's Graph does.
import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import type { DbImpact } from './types'

// what a change does to the data, as a tone: removing is red, reshaping amber, adding green
export const CHANGE_TONE: Record<string, string> = {
  dropped: 'var(--red)',
  altered: 'var(--amber)',
  added: 'var(--green)',
  written: 'var(--violet)',
  read: 'var(--dim)',
}
const SIGN: Record<string, string> = { dropped: '−', altered: '~', added: '+', written: 'w', read: 'r' }
// columns that only get read or written are table detail, not a change: they go in the table's tooltip
const SHAPE = new Set(['added', 'altered', 'dropped'])

export type Node = SimulationNodeDatum & { id: string; kind: 'pr' | 'table' | 'column'; label: string; change: string; tip: string; table: string }
export type Link = SimulationLinkDatum<Node> & { source: Node | string; target: Node | string; kind: 'touch' | 'column' | 'ref' }

const list = <T,>(v: T[] | undefined): T[] => (Array.isArray(v) ? v : [])

export function build(db: DbImpact, number: number) {
  const nodes: Node[] = [{ id: 'pr', kind: 'pr', label: `#${number}`, change: '', tip: `PR #${number}`, table: '', fx: 0, fy: 0 }]
  const links: Link[] = []
  const tables = list(db.tables).filter((t) => t.name)
  const names = new Set(tables.map((t) => t.name))
  for (const t of tables) {
    const id = `t:${t.name}`
    const cols = list(t.columns).filter((c) => c.name)
    const tip = [`${t.name}: ${t.change || '?'}`, ...cols.map((c) => `${SIGN[c.change || ''] || '·'} ${c.name}${c.note ? ` — ${c.note}` : ''}`)]
    nodes.push({ id, kind: 'table', label: t.name!, change: t.change || '', tip: tip.join('\n'), table: t.name! })
    links.push({ source: 'pr', target: id, kind: 'touch' })
    for (const c of cols.filter((c) => SHAPE.has(c.change || ''))) {
      const cid = `${id}.${c.name}`
      nodes.push({ id: cid, kind: 'column', label: `${SIGN[c.change!]} ${c.name}`, change: c.change!, tip: `${t.name}.${c.name}: ${c.change}${c.note ? ` — ${c.note}` : ''}`, table: t.name! })
      links.push({ source: id, target: cid, kind: 'column' })
    }
    // a foreign key to a table this PR does not touch is not drawn: the graph is about the change
    for (const r of new Set(list(t.refs))) {
      if (r !== t.name && names.has(r)) links.push({ source: id, target: `t:${r}`, kind: 'ref' })
    }
  }
  return { nodes, links }
}

