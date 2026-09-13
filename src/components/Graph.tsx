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
import { useEffect, useMemo, useRef, useState } from 'react'
import type { VisSection } from '../board'
import { avatar, PALETTE, rowState } from '../tokens'
import type { Row } from '../types'

type Node = SimulationNodeDatum & { id: string; kind: 'pr' | 'repo' | 'author'; label: string; degree: number }
type Link = SimulationLinkDatum<Node> & { source: Node; target: Node }

const lines = (r?: Row) => (r?.add ?? 0) + (r?.del ?? 0)
// most urgent first: a cluster's glow takes the colour of its most urgent PR
const URGENCY = ['error', 'changes', 'awaiting', 'running', 'commented', 'approved', 'idle']

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
  // REVIEWED comes already cut to the history window; a PR still open elsewhere keeps its open row, one node per url
  const rows = useMemo(() => {
    const byUrl = new Map<string, Row>()
    for (const r of secs.flatMap((s) => s.prs)) if (!byUrl.has(r.url) || byUrl.get(r.url)!.section === 'REVIEWED') byUrl.set(r.url, r)
    return [...byUrl.values()]
  }, [secs])
  const key = rows.map((r) => `${r.url}\t${r.author}`).sort().join('|')
  const svgRef = useRef<SVGSVGElement>(null)
  const sim = useRef<Simulation<Node, Link> | null>(null)
  const graph = useRef<{ nodes: Node[]; links: Link[] }>({ nodes: [], links: [] })
  // highlights rather than filters: dropping nodes would re-run the layout on every keystroke
  const [query, setQuery] = useState('')
  const latest = useRef({ rows, sel, onSelect, query })
  latest.current = { rows, sel, onSelect, query }

  // Look: colors, sizes, selection, tooltips. Cheap, runs on every poll, never restarts the layout.
  // Rows are looked up by url here rather than pinned on the node: the structure effect only re-runs
  // when the set of PRs changes, so a pinned row went stale on review state and its index-based uid.
  const paint = () => {
    const svg = svgRef.current
    if (!svg) return
    const { rows, sel, query } = latest.current
    const byUrl = new Map(rows.map((r) => [r.url, r]))
    // same fields as the board's filter box; a hit lights its PR and that PR's repo and author hubs
    const q = query.trim().toLowerCase()
    const hits = new Set<string>()
    for (const r of rows) {
      if (!q || !`${r.title} ${r.repo} ${r.author} #${r.number}`.toLowerCase().includes(q)) continue
      hits.add(r.url).add(`repo:${r.repo}`).add(`author:${r.author}`)
    }
    select(svg).classed('search', !!q)
    select(svg).selectAll<SVGLineElement, Link>('line').classed('hit', (l) => hits.has(l.source.id)) // a link's source is always its PR
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
      .classed('hit', (n) => hits.has(n.id))
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
    select(svg)
      .selectAll<SVGCircleElement, Node>('circle.halo')
      .style('fill', (h) => {
        const keys = rows.filter((r) => `repo:${r.repo}` === h.id).map((r) => rowState(r).key)
        return (PALETTE[URGENCY.find((k) => keys.includes(k)) || 'idle'] || PALETTE.idle).fg
      })
    sim.current?.force('collide', forceCollide<Node>((n) => radius(n) + 3))
  }

  // Structure: rebuilt when the set of PRs changes. Nodes that survive keep their position.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const old = new Map(graph.current.nodes.map((n) => [n.id, n]))
    const g = build(latest.current.rows)
    // A new node spawns by its repo, and each repo gets its own spot on a wide ring, so clusters start
    // apart instead of untangling from one pile in the middle. An author starts by its first PR's repo.
    const repos = [...new Set(latest.current.rows.map((r) => r.repo))].sort()
    const ring = 90 * Math.sqrt(repos.length)
    const spot = new Map(repos.map((repo, i) => {
      const a = (2 * Math.PI * i) / repos.length
      return [repo, { x: ring * Math.cos(a), y: ring * Math.sin(a) }]
    }))
    const home = new Map<string, { x: number; y: number }>()
    for (const r of latest.current.rows) {
      const at = spot.get(r.repo)!
      home.set(`repo:${r.repo}`, at)
      home.set(r.url, at)
      if (!home.has(`author:${r.author}`)) home.set(`author:${r.author}`, at)
    }
    for (const n of g.nodes) {
      const was = old.get(n.id)
      const at = home.get(n.id)
      if (was) Object.assign(n, { x: was.x, y: was.y, vx: was.vx, vy: was.vy })
      else if (at) Object.assign(n, { x: at.x + Math.random() * 30 - 15, y: at.y + Math.random() * 30 - 15 })
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

    // A glow behind each repo cluster, sized on every tick to reach its farthest PR.
    const members = new Map<Node, Node[]>()
    for (const l of g.links) if (l.target.kind === 'repo') members.set(l.target, [...(members.get(l.target) || [l.target]), l.source])
    const halo = world
      .select('g.halos')
      .selectAll<SVGCircleElement, Node>('circle.halo')
      .data([...members.keys()], (n) => n.id)
      .join('circle')
      .attr('class', 'halo')

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
      // hubs push harder than PRs and the pull to the centre is gentle, so repo clusters sit apart
      .force('charge', forceManyBody<Node>().strength((n) => (n.kind === 'pr' ? -160 : -600)))
      .force('link', forceLink<Node, Link>(g.links).distance((l) => (l.target.kind === 'repo' ? 40 : 90)))
      .force('x', forceX(0).strength(0.025))
      .force('y', forceY(0).strength(0.025))
      .on('tick', () => {
        link
          .attr('x1', (l) => l.source.x!)
          .attr('y1', (l) => l.source.y!)
          .attr('x2', (l) => l.target.x!)
          .attr('y2', (l) => l.target.y!)
        node.attr('transform', (n) => `translate(${n.x},${n.y})`)
        halo.each(function (h) {
          const ms = members.get(h)!
          const cx = ms.reduce((a, m) => a + m.x!, 0) / ms.length
          const cy = ms.reduce((a, m) => a + m.y!, 0) / ms.length
          const r = Math.max(...ms.map((m) => Math.hypot(m.x! - cx, m.y! - cy))) + 40
          select(this).attr('cx', cx).attr('cy', cy).attr('r', r)
        })
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
        <div className="search">
          <span className="mono" style={{ fontSize: 12, color: 'var(--dim3)' }}>
            /
          </span>
          <input
            id="q"
            value={query}
            placeholder="find by title, repo, author"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
          />
        </div>
        {!rows.length ? (
          'nothing to graph yet'
        ) : (
          `${rows.length} PRs · ${new Set(rows.map((r) => r.repo)).size} repos · ${new Set(rows.map((r) => r.author)).size} authors`
        )}
      </div>
      <svg ref={svgRef}>
        <defs>
          <filter id="halo-blur" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="18" />
          </filter>
        </defs>
        <g className="world">
          <g className="halos" />
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
        <span>size = lines changed · glow = a repo's most urgent PR · scroll to zoom · drag to move</span>
      </div>
    </div>
  )
}
