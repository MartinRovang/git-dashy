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
import type { Row } from '../types'

type Node = SimulationNodeDatum & { id: string; kind: 'pr' | 'repo' | 'author'; label: string; degree: number }
type Link = SimulationLinkDatum<Node> & { source: Node; target: Node }

const lines = (r?: Row) => (r?.add ?? 0) + (r?.del ?? 0)

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
    const pr: Node = { id: r.url, kind: 'pr', label: `#${r.number}`, degree: 2 }
    nodes.push(pr)
    // an author whose account is gone has no login; skip the hub rather than pool them all on a blank one
    for (const h of [hub('repo', r.repo), ...(r.author ? [hub('author', r.author)] : [])]) {
      h.degree++
      links.push({ source: pr, target: h })
    }
  }
  return { nodes, links }
}

export function Graph({ secs, sel, onSelect }: {
  secs: VisSection[]
  sel: string
  onSelect: (uid: string) => void
}) {
  // REVIEWED is history, and an open PR there would be a second node for the same url
  const rows = useMemo(() => secs.filter((s) => s.name !== 'REVIEWED').flatMap((s) => s.prs), [secs])
  const key = rows.map((r) => `${r.url}\t${r.author}`).sort().join('|')
  const svgRef = useRef<SVGSVGElement>(null)
  const sim = useRef<Simulation<Node, Link> | null>(null)
  const graph = useRef<{ nodes: Node[]; links: Link[] }>({ nodes: [], links: [] })
  const latest = useRef({ rows, sel, onSelect })
  latest.current = { rows, sel, onSelect }

  // Look: colors, sizes, selection, tooltips. Cheap, runs on every poll, never restarts the layout.
  // Rows are looked up by url here rather than pinned on the node: the structure effect only re-runs
  // when the set of PRs changes, so a pinned row went stale on review state and its index-based uid.
  const paint = () => {
    const svg = svgRef.current
    if (!svg) return
    const { rows, sel } = latest.current
    const byUrl = new Map(rows.map((r) => [r.url, r]))
    const max = Math.max(1, ...rows.map(lines))
    const radius = (n: Node) => {
      if (n.kind !== 'pr') return 4 + 2.2 * Math.sqrt(n.degree)
      return 4 + 10 * Math.sqrt(lines(byUrl.get(n.id)) / max)
    }
    const fg = (n: Node) => (PALETTE[rowState(byUrl.get(n.id)!).key] || PALETTE.idle).fg
    const node = select(svg)
      .selectAll<SVGGElement, Node>('g.gnode')
      .classed('sel', (n) => byUrl.get(n.id)?.uid === sel)
      .classed('hub', (n) => n.kind !== 'pr')
    node.select('text').attr('y', (n) => radius(n) + 3)
    node.select('title').text((n) => {
      const r = byUrl.get(n.id)
      return r ? `#${r.number} ${r.title}\n${r.repo} · ${r.author}` : n.label
    })
    node
      .select<SVGCircleElement>('circle')
      .attr('r', radius)
      .style('fill', (n) => {
        if (n.kind === 'repo') return 'var(--dim2)'
        if (n.kind === 'author') return avatar(n.label)
        return fg(n)
      })
      .style('stroke', (n) => (n.kind !== 'pr' ? 'none' : byUrl.get(n.id)?.uid === sel ? 'var(--ink)' : fg(n)))
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

    // Hover: the node and its neighbours stay lit, everything else fades.
    const near = new Map<string, Set<string>>()
    const edge = (a: string, b: string) => (near.get(a) || near.set(a, new Set([a])).get(a)!).add(b)
    for (const l of g.links) {
      edge(l.source.id, l.target.id)
      edge(l.target.id, l.source.id)
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
        const r = latest.current.rows.find((r) => r.url === n.id)
        if (r) latest.current.onSelect(r.uid)
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

  const states = (['approved', 'changes', 'commented', 'awaiting', 'running', 'error', 'idle'] as const).filter((k) =>
    rows.some((r) => rowState(r).key === k),
  )
  return (
    <div className="graph">
      <div className="gbadge">
        {!rows.length ? (
          'nothing to graph yet'
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
