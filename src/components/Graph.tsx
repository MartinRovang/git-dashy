// The whole board as a live graph, Obsidian/Quartz style: every PR is a node linked to a hub for its
// repo and a hub for its author, so shared repos and authors pull together. PRs are sized by lines
// changed and colored by review state. Drag nodes, scroll to zoom, hover to light up neighbours.
//
// ponytail: d3-force + SVG, not Quartz's PixiJS canvas. A board is tens of PRs, not thousands of
// notes; switch the drawing to canvas if a board ever gets big enough for SVG to stutter.
import { drag } from 'd3-drag'
import type { Simulation, SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force'
import { select } from 'd3-selection'
import { zoom } from 'd3-zoom'
import { useEffect, useMemo, useRef } from 'react'
import type { VisSection } from '../board'
import { avatar, PALETTE, rowState } from '../tokens'
import type { Row, Size } from '../types'

type Node = SimulationNodeDatum & { id: string; kind: 'pr' | 'repo' | 'author'; label: string; row?: Row; degree: number }
type Link = SimulationLinkDatum<Node> & { source: Node; target: Node }

const lines = (s?: Size) => (s && s.add != null && s.del != null ? s.add + s.del : null)

function build(rows: Row[]): { nodes: Node[]; links: Link[] } {
  const hubs = new Map<string, Node>()
  const nodes: Node[] = []
  const links: Link[] = []
  const hub = (kind: 'repo' | 'author', label: string) => {
    const id = `${kind}:${label}`
    let n = hubs.get(id)
    if (!n) {
      n = { id, kind, label, degree: 0 }
      hubs.set(id, n)
      nodes.push(n)
    }
    return n
  }
  for (const r of rows) {
    const pr: Node = { id: r.url, kind: 'pr', label: `#${r.number}`, row: r, degree: 2 }
    nodes.push(pr)
    for (const h of [hub('repo', r.repo), hub('author', r.author)]) {
      h.degree++
      links.push({ source: pr, target: h })
    }
  }
  return { nodes, links }
}

export function Graph({ secs, sizes, measuring, sel, onSelect }: {
  secs: VisSection[]
  sizes: Record<string, Size>
  measuring: boolean
  sel: string
  onSelect: (uid: string) => void
}) {
  // REVIEWED is history, and an open PR there would be a second node for the same url
  const rows = useMemo(() => secs.filter((s) => s.name !== 'REVIEWED').flatMap((s) => s.prs), [secs])
  const key = rows.map((r) => `${r.url}\t${r.author}`).sort().join('|')
  const svgRef = useRef<SVGSVGElement>(null)
  const sim = useRef<Simulation<Node, Link> | null>(null)
  const graph = useRef<{ nodes: Node[]; links: Link[] }>({ nodes: [], links: [] })
  const latest = useRef({ rows, sizes, sel, onSelect })
  latest.current = { rows, sizes, sel, onSelect }

  const radius = (n: Node) => {
    if (n.kind !== 'pr') return 4 + 2.2 * Math.sqrt(n.degree)
    const { rows, sizes } = latest.current
    const max = Math.max(1, ...rows.map((r) => lines(sizes[r.url]) || 0))
    const l = lines(sizes[n.id])
    return l == null ? 4 : 4 + 10 * Math.sqrt(l / max)
  }

  // Look: colors, sizes, selection. Cheap, runs on every poll, never restarts the layout.
  const paint = () => {
    const svg = svgRef.current
    if (!svg) return
    const { sizes, sel } = latest.current
    const node = select(svg)
      .selectAll<SVGGElement, Node>('g.gnode')
      .classed('sel', (n) => n.row?.uid === sel)
      .classed('hub', (n) => n.kind !== 'pr')
    node.select('text').attr('y', (n) => radius(n) + 3)
    node
      .select<SVGCircleElement>('circle')
      .attr('r', radius)
      .style('fill', (n) => {
        if (n.kind === 'repo') return 'var(--dim2)'
        if (n.kind === 'author') return avatar(n.label)
        if (lines(sizes[n.id]) == null) return 'var(--bg)'
        return (PALETTE[rowState(n.row!).key] || PALETTE.idle).fg
      })
      .style('stroke', (n) => (n.kind === 'pr' ? (PALETTE[rowState(n.row!).key] || PALETTE.idle).fg : 'none'))
    sim.current?.force('collide', forceCollide<Node>((n) => radius(n) + 3))
  }

  // Structure: rebuilt when the set of PRs changes. Nodes that survive keep their position.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const old = new Map(graph.current.nodes.map((n) => [n.id, n]))
    const g = build(latest.current.rows)
    for (const n of g.nodes) {
      const was = old.get(n.id)
      if (was) Object.assign(n, { x: was.x, y: was.y, vx: was.vx, vy: was.vy })
    }
    graph.current = g

    const root = select(svg)
    const world = root.select<SVGGElement>('g.world')
    const link = world
      .select('g.links')
      .selectAll<SVGLineElement, Link>('line')
      .data(g.links)
      .join('line')
    const node = world
      .select('g.nodes')
      .selectAll<SVGGElement, Node>('g.gnode')
      .data(g.nodes, (n) => n.id)
      .join((enter) => {
        const e = enter.append('g').attr('class', 'gnode')
        e.append('circle')
        e.append('text')
        e.append('title')
        return e
      })
    node.select('text').text((n) => n.label)
    node.select('title').text((n) => (n.row ? `#${n.row.number} ${n.row.title}\n${n.row.repo} · ${n.row.author}` : n.label))

    // Hover: the node and its neighbours stay lit, everything else fades.
    const near = new Map<string, Set<string>>()
    for (const l of g.links)
      for (const [a, b] of [[l.source, l.target], [l.target, l.source]]) {
        if (!near.has(a.id)) near.set(a.id, new Set([a.id]))
        near.get(a.id)!.add(b.id)
      }
    node
      .on('mouseenter', (_, n) => {
        const lit = near.get(n.id) || new Set([n.id])
        root.classed('focus', true)
        node.classed('lit', (m) => lit.has(m.id))
        link.classed('lit', (l) => l.source.id === n.id || l.target.id === n.id)
      })
      .on('mouseleave', () => {
        root.classed('focus', false)
        node.classed('lit', false)
        link.classed('lit', false)
      })
      .on('click', (_, n) => {
        if (n.row) latest.current.onSelect(n.row.uid)
      })

    const s = (sim.current ||= forceSimulation<Node, Link>())
    s.nodes(g.nodes)
      .force('charge', forceManyBody().strength(-160))
      .force('link', forceLink<Node, Link>(g.links).distance((l) => (l.target.kind === 'repo' ? 40 : 70)))
      .force('x', forceX(0).strength(0.04))
      .force('y', forceY(0).strength(0.04))
      .on('tick', () => {
        link
          .attr('x1', (l) => l.source.x!)
          .attr('y1', (l) => l.source.y!)
          .attr('x2', (l) => l.target.x!)
          .attr('y2', (l) => l.target.y!)
        node.attr('transform', (n) => `translate(${n.x},${n.y})`)
      })
    paint()
    s.alpha(old.size ? 0.4 : 1).restart()

    node.call(
      drag<SVGGElement, Node>()
        .on('start', (e, n) => {
          if (!e.active) s.alphaTarget(0.3).restart()
          n.fx = n.x
          n.fy = n.y
        })
        .on('drag', (e, n) => {
          n.fx = e.x
          n.fy = e.y
        })
        .on('end', (e, n) => {
          if (!e.active) s.alphaTarget(0)
          n.fx = null
          n.fy = null
        }),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(paint)

  // Zoom and pan, once. Labels fade in as you zoom, like Quartz, so a wide view stays uncluttered.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const root = select(svg)
    const fit = () => root.attr('viewBox', `${-svg.clientWidth / 2} ${-svg.clientHeight / 2} ${svg.clientWidth} ${svg.clientHeight}`)
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(svg)
    const z = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.25, 4])
      .on('zoom', ({ transform }) => {
        root.select('g.world').attr('transform', transform.toString())
        svg.style.setProperty('--label', String(Math.min(1, Math.max(0, (transform.k - 0.8) * 2))))
        svg.style.setProperty('--k', String(transform.k))
      })
    root.call(z).on('dblclick.zoom', null)
    svg.style.setProperty('--label', '0.4')
    svg.style.setProperty('--k', '1')
    return () => {
      ro.disconnect()
      root.on('.zoom', null)
      sim.current?.stop()
    }
  }, [])

  const measured = rows.filter((r) => lines(sizes[r.url]) != null).length
  const states = (['approved', 'changes', 'commented', 'awaiting', 'running', 'error', 'idle'] as const).filter((k) =>
    rows.some((r) => rowState(r).key === k),
  )
  return (
    <div className="graph">
      <div className="gbadge">
        {!rows.length ? (
          'nothing to graph yet'
        ) : measuring ? (
          <span>
            <i className="spinner" /> measuring diffs {measured}/{rows.length}
          </span>
        ) : (
          `${rows.length} PRs · ${new Set(rows.map((r) => r.repo)).size} repos · ${new Set(rows.map((r) => r.author)).size} authors`
        )}
      </div>
      <svg ref={svgRef}>
        <g className="world">
          <g className="links" />
          <g className="nodes" />
        </g>
      </svg>
      <div className="glegend">
        {states.map((k) => (
          <span key={k}>
            <i style={{ background: PALETTE[k].fg }} />
            {k}
          </span>
        ))}
        <span>
          <i style={{ background: 'var(--dim2)' }} /> repo
        </span>
        <span>
          <i className="gdot" /> author
        </span>
        <span>size = lines changed · scroll to zoom · drag to move</span>
      </div>
    </div>
  )
}
