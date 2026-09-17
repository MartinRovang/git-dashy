// A DB repo's whole schema (dbschema.rs) as a graph: a table per node, grouped by its Postgres schema, a link per
// foreign key. Columns are not nodes: components/SchemaGraph.tsx lists the chosen table's columns beside the graph.
//
// Pure: the nodes, links and column kinds only.
import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import { clean } from './dbgraph'
import type { DbImpact } from './types'

export type Column = { name: string; type: string; kind: Kind; ref: string }
/** `group`: the cluster inside its schema (cluster()), `space/<busiest table>`, or `space/` for a table linked to nothing there */
export type Table = SimulationNodeDatum & { id: string; space: string; short: string; degree: number; refs: string[]; columns: Column[]; group: string }
export type Ref = SimulationLinkDatum<Table> & { source: Table | string; target: Table | string }

export type Kind = 'pk' | 'fk' | 'text' | 'number' | 'time' | 'json' | 'bool' | 'uuid' | 'array' | 'binary' | 'other'

// first match wins: an array of anything is an array before it is its element type
const TYPES: [Kind, RegExp][] = [
  ['array', /\[\]|^_|\barray\b/],
  ['json', /^jsonb?\b/],
  ['time', /^(timestamp|timestamptz|date|time|timetz|interval)\b/],
  ['number', /^(small|big)?(int|integer|serial)\d*\b|^(float|real|double|numeric|decimal|money)\b/],
  ['bool', /^bool(ean)?\b/],
  ['uuid', /^uuid\b/],
  ['binary', /^(bytea|blob|bit)\b/],
  ['text', /^(text|varchar|character|char|citext|name)\b/],
]

/** What a column holds, for its icon: its key first, then its type. */
export function kind(key: string, type: string): Kind {
  if (key === 'fk' || key === 'pk') return key
  const t = type.trim().toLowerCase()
  return TYPES.find(([, re]) => re.test(t))?.[0] ?? 'other'
}

/** `restricted.users` -> ['restricted', 'users']; an unqualified table is in `public`. */
export function split(name: string): [string, string] {
  const i = name.indexOf('.')
  return i < 0 ? ['public', name] : [name.slice(0, i), name.slice(i + 1)]
}

export function build(db: DbImpact) {
  const tables: Table[] = clean(db).tables.map((t) => {
    const [space, short] = split(t.name)
    const columns = t.columns.map((c) => ({ name: c.name, type: c.note, kind: kind(c.key, c.note), ref: c.ref }))
    return { id: t.name, space, short, degree: 0, refs: t.refs, columns, group: '' }
  })
  const byId = new Map(tables.map((t) => [t.id, t]))
  const links: Ref[] = []
  for (const t of tables) {
    for (const r of new Set(t.refs)) {
      const to = byId.get(r)
      if (!to || to === t) continue
      links.push({ source: t.id, target: r })
      t.degree++
      to.degree++
    }
  }
  const spaces = [...new Set(tables.map((t) => t.space))].sort()
  const group = cluster(tables)
  for (const t of tables) t.group = group.get(t.id)!
  return { tables, links, spaces }
}

/** Clusters inside each schema: Louvain over the foreign keys that stay in the schema, a pair of tables weighted by
 *  how many columns reference across it. Each is named for its busiest table; a table linked to nothing in its
 *  schema, or in a cluster of fewer than MIN, goes in its schema's `space/`. */
export function cluster(tables: Omit<Table, 'group'>[]): Map<string, string> {
  const byId = new Map(tables.map((t) => [t.id, t]))
  let w = new Map<string, Map<string, number>>()
  const add = (g: typeof w, a: string, b: string, x: number) => {
    const m = g.get(a) || g.set(a, new Map()).get(a)!
    m.set(b, (m.get(b) || 0) + x)
  }
  for (const t of tables) {
    for (const r of new Set(t.refs)) {
      const to = byId.get(r)
      if (!to || to === t || to.space !== t.space) continue
      const x = Math.max(1, t.columns.filter((c) => c.ref === r).length)
      add(w, t.id, r, x)
      add(w, r, t.id, x)
    }
  }
  const m2 = [...w.values()].reduce((a, m) => a + [...m.values()].reduce((x, y) => x + y, 0), 0)
  // which cluster each table is in; starts as one each, then follows its node up every level
  const top = new Map(tables.map((t) => [t.id, t.id]))
  for (let level = 0; level < 10; level++) {
    const nodes = [...w.keys()].sort()
    // a node's weight counts its links inside itself (the self loop a merge leaves), as Louvain's does
    const k = new Map(nodes.map((n) => [n, [...w.get(n)!.values()].reduce((a, b) => a + b, 0)]))
    const com = new Map(nodes.map((n) => [n, n]))
    const tot = new Map(k)
    let changed = false
    for (let pass = 0, moved = true; moved && pass < 30; pass++) {
      moved = false
      for (const n of nodes) {
        const own = com.get(n)!
        const into = new Map<string, number>()
        for (const [o, x] of w.get(n)!) if (o !== n) into.set(com.get(o)!, (into.get(com.get(o)!) || 0) + x)
        tot.set(own, tot.get(own)! - k.get(n)!)
        let [best, gain] = [own, (into.get(own) || 0) - (tot.get(own)! * k.get(n)!) / m2]
        for (const [c, x] of [...into].sort(([a], [b]) => a.localeCompare(b))) {
          const g = x - (tot.get(c)! * k.get(n)!) / m2
          if (g > gain + 1e-9) [best, gain] = [c, g]
        }
        tot.set(best, tot.get(best)! + k.get(n)!)
        if (best !== own) {
          com.set(n, best)
          moved = changed = true
        }
      }
    }
    if (!changed) break
    // the next level: a node per cluster, the weights between them summed
    const next: typeof w = new Map()
    for (const [a, m] of w) for (const [b, x] of m) add(next, com.get(a)!, com.get(b)!, x)
    w = next
    for (const [t, n] of top) if (com.has(n)) top.set(t, com.get(n)!)
  }
  const members = new Map<string, Omit<Table, 'group'>[]>()
  for (const t of tables) (members.get(top.get(t.id)!) || members.set(top.get(t.id)!, []).get(top.get(t.id)!)!).push(t)
  const out = new Map<string, string>()
  for (const ms of members.values()) {
    const hub = ms.reduce((a, b) => (b.degree > a.degree || (b.degree === a.degree && b.id < a.id) ? b : a))
    for (const t of ms) out.set(t.id, ms.length >= MIN ? `${t.space}/${hub.id}` : `${t.space}/`)
  }
  return out
}

/** Fewer tables than this is not a cluster worth a ring of its own. */
const MIN = 3

export type Hit = { table: string; column: string; type: string }

/** Tables and columns matching `query`, best first, and every table that matched either way (the graph lights those).
 *  A name that is the query beats one that starts with it, which beats one that holds it; a table beats a column. */
export function search(tables: Table[], query: string, limit = 12): { hits: Hit[]; lit: Set<string> } {
  const q = query.trim().toLowerCase()
  const lit = new Set<string>()
  if (!q) return { hits: [], lit }
  const rank = (name: string) => (name === q ? 0 : name.startsWith(q) ? 1 : name.includes(q) ? 2 : -1)
  const scored: [number, Hit][] = []
  for (const t of tables) {
    const r = rank(t.short) >= 0 ? rank(t.short) : rank(t.id)
    if (r >= 0) {
      lit.add(t.id)
      scored.push([r * 2, { table: t.id, column: '', type: '' }])
    }
    for (const c of t.columns) {
      const rc = rank(c.name)
      if (rc < 0) continue
      lit.add(t.id)
      scored.push([rc * 2 + 1, { table: t.id, column: c.name, type: c.type }])
    }
  }
  scored.sort((a, b) => a[0] - b[0] || (a[1].table + a[1].column).localeCompare(b[1].table + b[1].column))
  return { hits: scored.slice(0, limit).map(([, h]) => h), lit }
}

/** A column name in the column galaxy: every table that has one by that name, and the kind most of them are. */
export type ColumnNode = SimulationNodeDatum & { id: string; name: string; kind: Kind; tables: string[] }

// ties between kinds go to the one listed first: a key says more than its type
const KIND_ORDER: Kind[] = ['pk', 'fk', 'text', 'number', 'time', 'json', 'bool', 'uuid', 'array', 'binary', 'other']

export function columnNodes(tables: Table[]): ColumnNode[] {
  const by = new Map<string, { tables: string[]; kinds: Map<Kind, number> }>()
  for (const t of tables) {
    for (const c of t.columns) {
      const e = by.get(c.name) || by.set(c.name, { tables: [], kinds: new Map() }).get(c.name)!
      e.tables.push(t.id)
      e.kinds.set(c.kind, (e.kinds.get(c.kind) || 0) + 1)
    }
  }
  return [...by]
    .map(([name, e]) => {
      const kind = [...e.kinds].sort((a, b) => b[1] - a[1] || KIND_ORDER.indexOf(a[0]) - KIND_ORDER.indexOf(b[0]))[0][0]
      return { id: `col:${name}`, name, kind, tables: e.tables }
    })
    .sort((a, b) => a.name.localeCompare(b.name))
}
