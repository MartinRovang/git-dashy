// A DB repo's whole schema: tables on the left as a live graph, one cloud per Postgres schema, sized by their foreign
// keys; to their right a galaxy of every column name, grouped by kind, where the chosen table's columns light up. The
// panel lists that table's columns, filtered by name, type or kind. A foreign key column, or a
// table that points here, jumps to that table. dbschema.ts builds the graph.
import { drag } from 'd3-drag'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force'
import { select } from 'd3-selection'
import { zoom, zoomIdentity, type ZoomBehavior } from 'd3-zoom'
import { useEffect, useMemo, useRef, useState } from 'react'
import { build, columnNodes, search, split, type Column, type ColumnNode, type Hit, type Kind, type Ref, type Table } from '../dbschema'
import type { DbImpact } from '../types'

// what a column holds, and its dot's colour; in this order round the column spiral, so like sits by like
const KINDS: Record<Kind, [label: string, tone: string]> = {
  pk: ['primary key', 'var(--gold)'],
  fk: ['foreign key', 'var(--cyan)'],
  text: ['text', 'var(--ink3)'],
  number: ['number', 'var(--green)'],
  time: ['time', 'var(--amber)'],
  json: ['json', 'var(--violet)'],
  bool: ['bool', 'var(--pink)'],
  uuid: ['uuid', 'var(--blood)'],
  array: ['array', 'var(--dim)'],
  binary: ['binary', 'var(--dim)'],
  other: ['other', 'var(--dim2)'],
}
const ORDER = Object.keys(KINDS) as Kind[]
// the chosen table's tie to one of its column nodes, and to the table that column's foreign key points at
type Tie = { t: Table; c: ColumnNode; to?: Table }
// the same table glyph as the PR's database graph, in a unit box centred on 0
const TABLE = 'M-.6,-.5H.6V.5H-.6ZM-.48,-.2H.48V.38H-.48ZM-.06,-.2H.06V.38H-.06Z'
type Placed = Ref & { source: Table; target: Table }

export function SchemaGraph({ db }: { db: DbImpact }) {
  const g = useMemo(() => build(db), [db])
  const [sel, setSel] = useState('')
  const [query, setQuery] = useState('')
  // the results list under the search box: shown while typing, the arrow keys move `at`, Enter or a click picks
  const [listing, setListing] = useState(false)
  const [at, setAt] = useState(0)
  // the column filter: kept while hopping between tables, so "every json column" can be read table by table
  const [colQuery, setColQuery] = useState('')
  const [kinds, setKinds] = useState<Set<Kind>>(new Set())
  const found = useMemo(() => search(g.tables, query), [g, query])
  const svgRef = useRef<SVGSVGElement>(null)
  const jump = useRef<(id: string) => void>(() => {})
  const goRef = useRef<(id: string) => void>(() => {})
  // a column name picked in the column galaxy: every table that has it stays lit, and the panel lists them
  const [colPick, setColPick] = useState('')
  const pickColRef = useRef<(name: string) => void>(() => {})
  pickColRef.current = (name) => setColPick((was) => (was === name ? '' : name))
  const showColumns = useRef<(table: string, cols: Column[]) => void>(() => {})

  // no two schemas share a colour: hues spread round the wheel, as the board's galaxies
  const tint = (space: string, light = 60) => `hsl(${(g.spaces.indexOf(space) * 360) / Math.max(1, g.spaces.length) + 190} 75% ${light}%)`

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const { tables, spaces } = g
    const links = g.links.map((l) => ({ ...l })) as Placed[]
    const root = select(svg)
    const world = root.select<SVGGElement>('g.world')
    const busiest = Math.max(1, ...tables.map((t) => t.degree))
    const radius = (t: Table) => 6 + 24 * Math.sqrt(t.degree / busiest)

    // Two rings. Inside a schema its biggest cluster sits in the middle and the rest round it, each sized by its tables;
    // the schemas sit round the middle of the view, each as wide as its clusters. Tables are pulled to their cluster.
    const groups = [...new Set(tables.map((t) => t.group))]
    const members = new Map(groups.map((c) => [c, tables.filter((t) => t.group === c)]))
    const greach = (c: string) => 16 + 20 * Math.sqrt(members.get(c)!.length)
    const offset = new Map<string, { x: number; y: number }>()
    const reach = new Map<string, number>()
    for (const s of spaces) {
      // biggest first; the loose tables (`space/`) go last, round the edge
      const mine = groups.filter((c) => c.startsWith(`${s}/`)).sort((a, b) => +a.endsWith('/') - +b.endsWith('/') || members.get(b)!.length - members.get(a)!.length)
      const [mid, ...rest] = mine
      offset.set(mid, { x: 0, y: 0 })
      const outer = Math.max(0, ...rest.map(greach))
      const around = rest.reduce((a, c) => a + 2 * greach(c), 0) * 1.1
      const r = rest.length ? Math.max(greach(mid) + outer + 24, around / (2 * Math.PI)) : 0
      rest.forEach((c, i) => {
        const a = (2 * Math.PI * i) / rest.length - Math.PI / 2
        offset.set(c, { x: r * Math.cos(a), y: r * Math.sin(a) })
      })
      reach.set(s, (rest.length ? r + outer : greach(mid)) + 30)
    }
    // neighbours on a ring of n sit 2·ring·sin(π/n) apart: far enough that the widest two never meet
    const ring = spaces.length > 1 ? Math.max(...reach.values()) / Math.sin(Math.PI / spaces.length) + 40 : 0
    const center = new Map(
      spaces.map((s, i) => {
        const a = (2 * Math.PI * i) / spaces.length - Math.PI / 2
        return [s, { x: ring * Math.cos(a), y: ring * Math.sin(a) }]
      }),
    )
    const spot = new Map(groups.map((c) => {
      const s = center.get(c.slice(0, c.indexOf('/')))!
      const o = offset.get(c)!
      return [c, { x: s.x + o.x, y: s.y + o.y }]
    }))
    for (const t of tables) {
      const p = spot.get(t.group)!
      t.x ??= p.x + (Math.random() - 0.5) * 40
      t.y ??= p.y + (Math.random() - 0.5) * 40
    }

    const sim = forceSimulation<Table>(tables)
      .force('charge', forceManyBody<Table>().strength(-70).distanceMax(160))
      // a foreign key holds a cluster together; across clusters or schemas it only leans, the rings keep the shape
      .force('link', forceLink<Table, Placed>(links).id((t) => t.id).distance(50).strength((l) => (l.source.group === l.target.group ? 0.3 : l.source.space === l.target.space ? 0.03 : 0.005)))
      .force('collide', forceCollide<Table>((t) => radius(t) + 4))
      .force('x', forceX<Table>((t) => spot.get(t.group)!.x).strength(0.15))
      .force('y', forceY<Table>((t) => spot.get(t.group)!.y).strength(0.15))

    const halo = world.select('g.halos').selectAll<SVGCircleElement, string>('circle').data(spaces).join('circle').style('fill', (s) => `url(#schalo-${spaces.indexOf(s)})`)
    const name = world
      .select('g.gnames')
      .selectAll<SVGTextElement, string>('text')
      .data(spaces)
      .join('text')
      .text((s) => s)
      .style('fill', (s) => tint(s, 70))
    // a cluster: a faint dashed ring in its schema's colour, named for its busiest table
    const hubOf = (c: string) => c.slice(c.indexOf('/') + 1)
    const ringOf = world.select('g.clusters').selectAll<SVGCircleElement, string>('circle').data(groups).join('circle').style('stroke', (c) => tint(c.slice(0, c.indexOf('/'))))
    const cname = world
      .select('g.cnames')
      .selectAll<SVGTextElement, string>('text')
      .data(groups)
      .join('text')
      .text((c) => (hubOf(c) ? `${split(hubOf(c))[1]} · ${members.get(c)!.length}` : `unclustered · ${members.get(c)!.length}`))
      .style('fill', (c) => tint(c.slice(0, c.indexOf('/')), 72))
    const link = world.select('g.links').selectAll<SVGLineElement, Placed>('line').data(links).join('line')
    const node = world
      .select('g.nodes')
      .selectAll<SVGGElement, Table>('g.gnode')
      .data(tables, (t) => t.id)
      .join((enter) => {
        const e = enter.append('g').attr('class', 'gnode')
        e.append('circle')
        e.append('path').attr('class', 'icon').attr('fill-rule', 'evenodd')
        e.append('text')
        e.append('title')
        return e
      })
    node.select('title').text((t) => `${t.id}\n${t.columns.length} columns · ${t.degree} foreign keys`)
    node.select('text').text((t) => t.short).attr('y', (t) => radius(t) + 3)
    node
      .select('circle')
      .attr('r', radius)
      .style('fill', (t) => `color-mix(in srgb, ${tint(t.space)} 25%, var(--bg))`)
      .style('stroke', (t) => tint(t.space))

    node
      .select('path.icon')
      .attr('d', TABLE)
      .attr('transform', (t) => `scale(${radius(t) * 0.8})`)
      .style('fill', (t) => tint(t.space, 70))

    // hover: a table and the tables it links to stay lit
    const near = new Map<string, Set<string>>()
    const edge = (a: string, b: string) => (near.get(a) || near.set(a, new Set([a])).get(a)!).add(b)
    for (const l of links) {
      edge(l.source.id, l.target.id)
      edge(l.target.id, l.source.id)
    }
    const light = (id: string | null) => {
      const lit = id ? near.get(id) || new Set([id]) : null
      root.classed('focus', !!lit)
      node.classed('lit', (t) => !!lit?.has(t.id))
      link.classed('lit', (l) => !!id && (l.source.id === id || l.target.id === id))
      col.classed('lit', false)
    }
    node.on('mouseenter', (_, t) => light(t.id)).on('mouseleave', () => light(null)).on('click', (_, t) => setSel(t.id))

    // The column galaxy, to the right of every schema: a node per column name, sized by how many tables have one,
    // grouped by kind round a ring of its own. The chosen table's columns light up in it, with a faint line back to the
    // table and, from a foreign key column, a cyan one to the table it points at. A column node lights every table
    // that has it; a click keeps them lit and lists them in the panel.
    const byId = new Map(tables.map((t) => [t.id, t]))
    const cols = columnNodes(tables)
    const colByName = new Map(cols.map((c) => [c.name, c]))
    const most = Math.max(1, ...cols.map((c) => c.tables.length))
    const cr = (c: ColumnNode) => 3 + 11 * Math.sqrt(c.tables.length / most)
    const kindsHere = ORDER.filter((kd) => cols.some((c) => c.kind === kd))
    const byKind = new Map(kindsHere.map((kd) => [kd, cols.filter((c) => c.kind === kd)]))
    const kreach = (kd: Kind) => 16 + 12 * Math.sqrt(byKind.get(kd)!.length)
    const kring = kindsHere.length > 1 ? Math.max(...kindsHere.map(kreach)) / Math.sin(Math.PI / kindsHere.length) + 20 : 0
    const right = ring + Math.max(...reach.values())
    const colReach = kring + Math.max(0, ...kindsHere.map(kreach))
    const anchor = { x: right + colReach + 160, y: 0 }
    const kspot = new Map(
      kindsHere.map((kd, i) => {
        const a = (2 * Math.PI * i) / kindsHere.length - Math.PI / 2
        return [kd, { x: anchor.x + kring * Math.cos(a), y: anchor.y + kring * Math.sin(a) }]
      }),
    )
    for (const c of cols) {
      const p = kspot.get(c.kind)!
      c.x ??= p.x + (Math.random() - 0.5) * 30
      c.y ??= p.y + (Math.random() - 0.5) * 30
    }
    const colSim = forceSimulation<ColumnNode>(cols)
      .force('charge', forceManyBody<ColumnNode>().strength(-14).distanceMax(90))
      .force('collide', forceCollide<ColumnNode>((c) => cr(c) + 2))
      .force('x', forceX<ColumnNode>((c) => kspot.get(c.kind)!.x).strength(0.12))
      .force('y', forceY<ColumnNode>((c) => kspot.get(c.kind)!.y).strength(0.12))
    const kname = world
      .select('g.knames')
      .selectAll<SVGTextElement, Kind>('text')
      .data(kindsHere)
      .join('text')
      .text((kd) => `${KINDS[kd][0]} · ${byKind.get(kd)!.length}`)
      .style('fill', (kd) => KINDS[kd][1])
    const colTitle = world.select<SVGTextElement>('text.colhint').attr('x', anchor.x).text(`${cols.length} columns`)
    const col = world
      .select('g.cols')
      .selectAll<SVGGElement, ColumnNode>('g.gcol')
      .data(cols, (c) => c.id)
      .join((enter) => {
        const e = enter.append('g').attr('class', 'gcol')
        e.append('circle')
        e.append('text')
        e.append('title')
        return e
      })
    col.select('circle').attr('r', cr).style('fill', (c) => KINDS[c.kind][1])
    col.select('text').text((c) => c.name).attr('x', (c) => cr(c) + 3)
    col
      .select('title')
      .text((c) => `${c.name} · in ${c.tables.length} table${c.tables.length === 1 ? '' : 's'}\n${c.tables.slice(0, 15).join('\n')}${c.tables.length > 15 ? '\n…' : ''}`)
    col
      .on('mouseenter', (_, c) => {
        const has = new Set(c.tables)
        root.classed('focus', true)
        node.classed('lit', (t) => has.has(t.id))
        link.classed('lit', false)
        col.classed('lit', (x) => x === c)
      })
      .on('mouseleave', () => light(null))
      .on('click', (_, c) => pickColRef.current(c.name))

    let ties: Tie[] = []
    const lines = () => {
      world
        .select('g.tether')
        .selectAll<SVGLineElement, Tie>('line')
        .attr('x1', (d) => d.t.x!)
        .attr('y1', (d) => d.t.y!)
        .attr('x2', (d) => d.c.x!)
        .attr('y2', (d) => d.c.y!)
      world
        .select('g.fklines')
        .selectAll<SVGLineElement, Tie>('line')
        .attr('x1', (d) => d.c.x!)
        .attr('y1', (d) => d.c.y!)
        .attr('x2', (d) => d.to!.x!)
        .attr('y2', (d) => d.to!.y!)
    }
    showColumns.current = (id, shown) => {
      const owner = byId.get(id)
      ties = owner ? shown.flatMap((c) => (colByName.has(c.name) ? [{ t: owner, c: colByName.get(c.name)!, to: byId.get(c.ref) }] : [])) : []
      const mine = new Set(ties.map((x) => x.c))
      root.classed('colsel', !!owner)
      col.classed('sel', (c) => mine.has(c))
      world.select('g.tether').selectAll<SVGLineElement, Tie>('line').data(ties).join('line')
      world.select('g.fklines').selectAll<SVGLineElement, Tie>('line').data(ties.filter((x) => x.to)).join('line')
      lines()
    }
    colSim.on('tick', () => {
      for (const kd of kindsHere) {
        const mine = byKind.get(kd)!
        const cx = mine.reduce((a, c) => a + c.x!, 0) / mine.length
        const cy = mine.reduce((a, c) => a + c.y!, 0) / mine.length
        const r = Math.max(0, ...mine.map((c) => Math.hypot(c.x! - cx, c.y! - cy) + cr(c)))
        kname.filter((d) => d === kd).attr('x', cx).attr('y', cy + r + 8)
      }
      colTitle.attr('y', Math.min(...cols.map((c) => c.y! - cr(c))) - 30)
      col.attr('transform', (c) => `translate(${c.x},${c.y})`)
      lines()
    })
    col.call(
      drag<SVGGElement, ColumnNode>()
        .on('start', (e, c) => {
          if (!e.active) colSim.alphaTarget(0.3).restart()
          c.fx = c.x
          c.fy = c.y
        })
        .on('drag', (e, c) => {
          c.fx = e.x
          c.fy = e.y
        })
        .on('end', (e, c) => {
          if (!e.active) colSim.alphaTarget(0)
          c.fx = null
          c.fy = null
        }),
    )

    let k = 1
    // WebKit lays out every drawn label each tick: name the busy tables, and the rest once zoomed in
    const named = () => {
      node.classed('named', (t) => k >= 1.5 || t.degree >= busiest / 6)
      col.classed('named', (c) => k >= 1.8 || c.tables.length >= most / 4)
    }
    const circle = (mine: Table[], pad: number) => {
      const cx = mine.reduce((a, t) => a + t.x!, 0) / mine.length
      const cy = mine.reduce((a, t) => a + t.y!, 0) / mine.length
      return { cx, cy, r: Math.max(0, ...mine.map((t) => Math.hypot(t.x! - cx, t.y! - cy) + radius(t))) + pad }
    }
    sim.on('tick', () => {
      for (const c of groups) {
        const { cx, cy, r } = circle(members.get(c)!, 12)
        ringOf.filter((d) => d === c).attr('cx', cx).attr('cy', cy).attr('r', r)
        cname.filter((d) => d === c).attr('x', cx).attr('y', cy + r + 4)
      }
      for (const s of spaces) {
        const mine = tables.filter((t) => t.space === s)
        const cx = mine.reduce((a, t) => a + t.x!, 0) / mine.length
        const cy = mine.reduce((a, t) => a + t.y!, 0) / mine.length
        const r = Math.max(0, ...mine.map((t) => Math.hypot(t.x! - cx, t.y! - cy) + radius(t))) + 30
        halo.filter((d) => d === s).attr('cx', cx).attr('cy', cy).attr('r', r)
        name.filter((d) => d === s).attr('x', cx).attr('y', cy - r + 8)
      }
      link
        .attr('x1', (l) => l.source.x!)
        .attr('y1', (l) => l.source.y!)
        .attr('x2', (l) => l.target.x!)
        .attr('y2', (l) => l.target.y!)
      node.attr('transform', (t) => `translate(${t.x},${t.y})`)
      lines()
    })

    node.call(
      drag<SVGGElement, Table>()
        .on('start', (e, t) => {
          if (!e.active) sim.alphaTarget(0.3).restart()
          t.fx = t.x
          t.fy = t.y
        })
        .on('drag', (e, t) => {
          t.fx = e.x
          t.fy = e.y
        })
        .on('end', (e, t) => {
          if (!e.active) sim.alphaTarget(0)
          t.fx = null
          t.fy = null
        }),
    )

    const fit = () => root.attr('viewBox', `${-svg.clientWidth / 2} ${-svg.clientHeight / 2} ${svg.clientWidth} ${svg.clientHeight}`)
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(svg)
    const z: ZoomBehavior<SVGSVGElement, unknown> = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .filter((e) => !e.ctrlKey && !e.button)
      .on('zoom', ({ transform }) => {
        world.attr('transform', transform.toString())
        svg.style.setProperty('--k', String(transform.k))
        k = transform.k
        named()
      })
    root.call(z).on('dblclick.zoom', null)
    // start with every schema, and the column area to their right, in view
    const [left, far] = [-right, anchor.x + colReach + 120]
    const fitK = Math.min(1, svg.clientWidth / (far - left), svg.clientHeight / (2 * Math.max(right, colReach + 60)))
    root.call(z.transform, zoomIdentity.scale(fitK).translate(-(left + far) / 2, 0))
    jump.current = (id) => {
      const t = tables.find((t) => t.id === id)
      if (t) root.call(z.transform, zoomIdentity.scale(Math.max(k, 1.6)).translate(-t.x!, -t.y!))
    }
    return () => {
      sim.stop()
      colSim.stop()
      ro.disconnect()
      root.on('.zoom', null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [g])

  // the chosen table, and the tables the search finds, drawn over whatever hover does
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const root = select(svg)
    const q = query.trim().toLowerCase()
    const picked = new Set(g.tables.filter((t) => t.columns.some((c) => c.name === colPick)).map((t) => t.id))
    root.classed('search', !!q || !!colPick)
    // the chosen table and the tables it has a foreign key to or from stay bright; the rest dim
    const chosen = g.tables.find((t) => t.id === sel)
    const near = new Set(chosen ? [sel, ...chosen.refs, ...g.tables.filter((t) => t.refs.includes(sel)).map((t) => t.id)] : [])
    root.classed('tsel', !!chosen)
    root.selectAll<SVGGElement, Table>('g.gnode').classed('sel', (t) => t.id === sel).classed('near', (t) => near.has(t.id)).classed('hit', (t) => found.lit.has(t.id) || picked.has(t.id))
    root.selectAll<SVGGElement, ColumnNode>('g.gcol').classed('hit', (c) => c.name === colPick || (!!q && c.name.includes(q)))
    root.selectAll<SVGLineElement, Placed>('g.links line').classed('sel', (l) => l.source.id === sel || l.target.id === sel)
  }, [sel, found, query, colPick, g])

  const table = g.tables.find((t) => t.id === sel)
  const cq = colQuery.trim().toLowerCase()
  const shown = (table?.columns || []).filter(
    (c) => (!kinds.size || kinds.has(c.kind)) && (!cq || c.name.includes(cq) || c.type.includes(cq) || c.ref.includes(cq)),
  )
  const shownKey = shown.map((c) => c.name).join('|')
  useEffect(() => {
    showColumns.current(sel, shown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, shownKey, g])
  // chips for what this table has, and any still switched on
  const present = (Object.keys(KINDS) as Kind[]).filter((k) => kinds.has(k) || table?.columns.some((c) => c.kind === k))
  const flip = (k: Kind) =>
    setKinds((was) => {
      const now = new Set(was)
      if (!now.delete(k)) now.add(k)
      return now
    })
  // clusters biggest first, the unclustered last; one jumps to its busiest table
  const size = (c: string) => g.tables.filter((t) => t.group === c).length
  const clusters = [...new Set(g.tables.map((t) => t.group))].sort((a, b) => +a.endsWith('/') - +b.endsWith('/') || size(b) - size(a))
  const Cluster = ({ c }: { c: string }) => {
    const hub = c.slice(c.indexOf('/') + 1)
    return hub ? (
      <button className="cref mono" title={`go to ${hub}, the busiest table in this cluster`} onClick={() => go(hub)}>
        cluster {split(hub)[1]} · {size(c)}
      </button>
    ) : (
      <span className="mono dim">unclustered · {size(c)}</span>
    )
  }
  const pointedBy = table ? g.tables.filter((t) => t !== table && t.refs.includes(table.id)) : []
  const go = (id: string) => {
    setSel(id)
    jump.current(id)
  }
  goRef.current = go
  // a column hit opens its table with the column filter set to it, so the spiral and the panel show just that column
  const pick = (h: Hit) => {
    go(h.table)
    setColQuery(h.column)
    if (h.column) setKinds(new Set())
    setListing(false)
  }
  const onSearchKey = (e: React.KeyboardEvent) => {
    const n = found.hits.length
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setListing(true)
      if (n) setAt((i) => (i + (e.key === 'ArrowDown' ? 1 : n - 1)) % n)
    } else if (e.key === 'Enter' && n) pick(found.hits[Math.min(at, n - 1)])
  }

  return (
    <div className="schema">
      <div className="graph schemag">
        <div className="schfind">
          <div className="search">
            <input
              placeholder={`find a table or column…`}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setAt(0)
                setListing(true)
              }}
              onFocus={() => setListing(true)}
              onBlur={() => setListing(false)}
              onKeyDown={onSearchKey}
            />
          </div>
          {query.trim() && listing && (
            <ul className="schhits" role="listbox">
              <li className="sum mono">
                {found.lit.size} table{found.lit.size === 1 ? '' : 's'} lit
              </li>
              {found.hits.map((h, i) => (
                <li
                  key={h.table + '.' + h.column}
                  role="option"
                  aria-selected={i === at}
                  // mousedown, not click: the input's blur would close the list first
                  onMouseDown={(e) => {
                    e.preventDefault()
                    pick(h)
                  }}
                  onMouseEnter={() => setAt(i)}
                >
                  {h.column ? (
                    <>
                      <span className="hc">{h.column}</span>
                      <span className="ht mono">
                        {split(h.table)[1]} · {h.type}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="hn">{split(h.table)[1]}</span>
                      <span className="ht mono">{split(h.table)[0]} · table</span>
                    </>
                  )}
                </li>
              ))}
              {!found.hits.length && <li className="sum mono">nothing matches</li>}
            </ul>
          )}
        </div>
        <svg ref={svgRef} role="img" aria-label="tables grouped by schema, linked by foreign keys">
          <defs>
            {g.spaces.map((s, i) => (
              <radialGradient key={s} id={`schalo-${i}`}>
                <stop offset="0%" style={{ stopColor: tint(s), stopOpacity: 0.22 }} />
                <stop offset="100%" style={{ stopColor: tint(s), stopOpacity: 0 }} />
              </radialGradient>
            ))}
          </defs>
          <g className="world">
            <g className="halos" />
            <g className="clusters" />
            <g className="links" />
            <g className="nodes" />
            <g className="tether" />
            <g className="fklines" />
            <g className="cols" />
            <g className="knames" />
            <text className="colhint" />
            <g className="cnames" />
            <g className="gnames" />
          </g>
        </svg>
      </div>
      <aside className="schcols">
        {colPick && (
          <div className="schpick">
            <div className="sub">
              column <b className="mono">{colPick}</b> · in {g.tables.filter((t) => t.columns.some((c) => c.name === colPick)).length} tables
              <button className="cref mono" onClick={() => setColPick('')}>
                clear
              </button>
            </div>
            <div className="tags">
              {g.tables
                .filter((t) => t.columns.some((c) => c.name === colPick))
                .map((t) => (
                  <button className="tag" key={t.id} onClick={() => go(t.id)} style={{ borderColor: tint(t.space) }}>
                    {t.id}
                  </button>
                ))}
            </div>
          </div>
        )}
        {table ? (
          <>
            <div className="schhead">
              <em style={{ color: tint(table.space, 70) }}>{table.space}</em>
              <b>{table.short}</b>
              <span className="mono">
                {table.columns.length} columns · {table.degree} foreign keys
              </span>
              <Cluster c={table.group} />
            </div>
            <div className="schfilter">
              <div className="search">
                <input placeholder="filter columns…" value={colQuery} onChange={(e) => setColQuery(e.target.value)} />
              </div>
              <div className="tags">
                {present.map((k) => (
                  <button className="tag" key={k} aria-pressed={kinds.has(k)} onClick={() => flip(k)}>
                    <i className="kdot" style={{ background: KINDS[k][1] }} /> {KINDS[k][0]}
                  </button>
                ))}
              </div>
              {(cq || kinds.size > 0) && (
                <div className="sub">
                  {shown.length} of {table.columns.length}
                  <button className="cref mono" onClick={() => (setColQuery(''), setKinds(new Set()))}>
                    clear
                  </button>
                </div>
              )}
            </div>
            <ul>
              {shown.map((c) => {
                const to = c.ref && g.tables.some((t) => t.id === c.ref) ? c.ref : ''
                return (
                  <li key={c.name} title={KINDS[c.kind][0]}>
                    <span className="cn">{c.name}</span>
                    {c.kind === 'pk' && <span className="ck pk mono">pk</span>}
                    {to ? (
                      <button className="cref mono" title={`go to ${to}`} onClick={() => go(to)}>
                        → {to}
                      </button>
                    ) : (
                      <span className="ct mono">{c.type}</span>
                    )}
                  </li>
                )
              })}
            </ul>
            {pointedBy.length > 0 && (
              <>
                <div className="sub">referenced by</div>
                <div className="tags">
                  {pointedBy.map((t) => (
                    <button className="tag" key={t.id} onClick={() => go(t.id)} style={{ borderColor: tint(t.space) }}>
                      {t.id}
                    </button>
                  ))}
                </div>
              </>
            )}
          </>
        ) : (
          <div className="schhint">
            <p>Click a table to see its columns.</p>
            <div className="sub">schemas</div>
            {g.spaces.map((s) => (
              <div key={s}>
                <div className="schkey">
                  <i style={{ background: tint(s) }} /> <b>{s}</b> <span className="mono">{g.tables.filter((t) => t.space === s).length}</span>
                </div>
                {clusters
                  .filter((c) => c.startsWith(`${s}/`))
                  .map((c) => (
                    <div className="schkey sub2" key={c}>
                      <Cluster c={c} />
                    </div>
                  ))}
              </div>
            ))}
          </div>
        )}
      </aside>
    </div>
  )
}
