// The whole board as a live graph, Obsidian/Quartz style: every PR is a node linked to hubs, by default
// one for its repo, so each repo is a star of its PRs. The group-by tabs
// swap the hubs for the PR's author, kind (from its review) or review state. PRs are sized by lines
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

type Hub = 'repo' | 'author' | 'kind' | 'state'
type Node = SimulationNodeDatum & { id: string; kind: 'pr' | Hub; label: string; degree: number }
type Link = SimulationLinkDatum<Node> & { source: Node; target: Node; primary: boolean }
const GROUPS = ['repo', 'kind', 'author', 'state'] as const
type Group = (typeof GROUPS)[number]

const lines = (r?: Row) => (r?.add ?? 0) + (r?.del ?? 0)
// a person, head and shoulders, in a unit box centred on 0: author nodes scale it to their radius
const PERSON = 'M-.3,-.32a.3,.3 0 1,0 .6,0a.3,.3 0 1,0 -.6,0ZM-.62,.62Q-.62,.06 0,.06Q.62,.06 .62,.62Z'

// A title's conventional-commit prefix, for PRs no review has tagged yet: `feat(api)!: ...`
const PREFIX: Record<string, string> = {
  feat: 'feature', feature: 'feature', fix: 'fix', bugfix: 'fix', hotfix: 'fix', security: 'security', sec: 'security',
  perf: 'perf', refactor: 'refactor', docs: 'docs', doc: 'docs', test: 'tests', tests: 'tests', deps: 'deps',
  chore: 'maintenance', ci: 'maintenance', build: 'maintenance', style: 'maintenance', revert: 'maintenance',
}
/** The review's tag when there is one, else the title's prefix, else untagged. */
function tagOf(r: Row): { kind: string; breaking: boolean } {
  const m = /^(\w+)(\([^)]*\))?(!)?:/.exec(r.title)
  const guess = m ? PREFIX[m[1].toLowerCase()] : undefined
  return { kind: r.kind || guess || 'untagged', breaking: !!r.breaking || !!m?.[3] }
}

/** The hubs a PR links to, first one first: that one is its cluster. */
function hubsOf(r: Row, by: Group): [Hub, string][] {
  // an author whose account is gone has no login; skip the hub rather than pool them all on a blank one
  const author: [Hub, string][] = r.author ? [['author', r.author]] : []
  // repo is a plain star, the repo with its PRs around it; author clusters by author and keeps the repos
  if (by === 'repo') return [['repo', r.repo]]
  if (by === 'author') return [...author, ['repo', r.repo]]
  if (by === 'kind') return [['kind', tagOf(r).kind]]
  return [['state', rowState(r).key]]
}

function build(rows: Row[], by: Group): { nodes: Node[]; links: Link[] } {
  const hubs = new Map<string, Node>()
  const nodes: Node[] = []
  const links: Link[] = []
  for (const r of rows) {
    // the repo rides on the label, so a PR still says where it lives when grouped by anything but repo
    const pr: Node = { id: r.url, kind: 'pr', label: `${r.repo.split('/').pop()} #${r.number}`, degree: 2 }
    nodes.push(pr)
    hubsOf(r, by).forEach(([kind, label], i) => {
      const id = `${kind}:${label}`
      let h = hubs.get(id)
      if (!h) {
        h = { id, kind, label, degree: 0 }
        hubs.set(id, h)
        nodes.push(h)
      }
      h.degree++
      links.push({ source: pr, target: h, primary: i === 0 })
    })
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
  // ponytail: plain state, not localStorage: the GUI gets a new port, so a new origin and empty storage, every launch
  const [by, setBy] = useState<Group>('repo')
  const key = by + '|' + rows.map((r) => `${r.url}\t${hubsOf(r, by).join('\t')}`).sort().join('|')
  const lastBy = useRef(by)
  const svgRef = useRef<SVGSVGElement>(null)
  const sim = useRef<Simulation<Node, Link> | null>(null)
  const graph = useRef<{ nodes: Node[]; links: Link[] }>({ nodes: [], links: [] })
  // highlights rather than filters: dropping nodes would re-run the layout on every keystroke
  const [query, setQuery] = useState('')
  const latest = useRef({ rows, sel, onSelect, query, by })
  latest.current = { rows, sel, onSelect, query, by }

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
      if (!q || !`${r.title} ${r.repo} ${r.author} #${r.number} ${tagOf(r).kind}`.toLowerCase().includes(q)) continue
      hits.add(r.url)
      for (const [kind, label] of hubsOf(r, latest.current.by)) hits.add(`${kind}:${label}`)
    }
    select(svg).classed('search', !!q)
    select(svg).selectAll<SVGLineElement, Link>('line').classed('hit', (l) => hits.has(l.source.id)) // a link's source is always its PR
    const max = Math.max(1, ...rows.map(lines))
    // a hub grows with its PRs against the busiest hub, so the busiest hub is drawn largest
    const busiest = Math.max(1, ...graph.current.nodes.map((n) => (n.kind === 'pr' ? 0 : n.degree)))
    const radius = (n: Node) => {
      if (n.kind !== 'pr') return 8 + 22 * Math.sqrt(n.degree / busiest)
      return 4 + 10 * Math.sqrt(lines(byUrl.get(n.id)) / max)
    }
    const fg = (n: Node) => (PALETTE[rowState(byUrl.get(n.id)!).key] || PALETTE.idle).fg
    const node = select(svg)
      .selectAll<SVGGElement, Node>('g.gnode')
      .classed('sel', (n) => byUrl.get(n.id)?.uid === sel)
      .classed('hub', (n) => n.kind !== 'pr')
      .classed('hit', (n) => hits.has(n.id))
      .classed('breaking', (n) => !!byUrl.get(n.id) && tagOf(byUrl.get(n.id)!).breaking)
    node.select('text').attr('y', (n) => radius(n) + 3)
    node
      .select('path.person')
      .attr('display', (n) => (n.kind === 'author' ? null : 'none'))
      .attr('transform', (n) => `scale(${radius(n) * 0.75})`)
      .style('fill', (n) => avatar(n.label))
    node.select('title').text((n) => {
      const r = byUrl.get(n.id)
      if (!r) return n.label
      const t = tagOf(r)
      return `#${r.number} ${r.title}\n${r.repo} · ${r.author} · ${t.kind}${t.breaking ? ' · breaking' : ''}`
    })
    node
      .select<SVGCircleElement>('circle')
      .attr('r', radius)
      .style('fill', (n) => {
        if (n.kind === 'pr') return fg(n)
        const c = n.kind === 'author' ? avatar(n.label) : n.kind === 'state' ? (PALETTE[n.label] || PALETTE.idle).fg : 'var(--dim2)'
        // a hub reads disabled: its colour washed into the background, opaque so links stop at its edge
        return `color-mix(in srgb, ${c} 35%, var(--bg))`
      })
      .style('stroke', (n) => {
        if (n.kind !== 'pr') return 'none'
        if (byUrl.get(n.id)?.uid === sel) return 'var(--ink)'
        return tagOf(byUrl.get(n.id)!).breaking ? 'var(--red)' : fg(n)
      })
    sim.current?.force('collide', forceCollide<Node>((n) => radius(n) + 3))
  }

  // Structure: rebuilt when the set of PRs changes. Nodes that survive keep their position.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const old = new Map(graph.current.nodes.map((n) => [n.id, n]))
    const { rows, by } = latest.current
    const g = build(rows, by)
    // A new node spawns by its cluster's hub, and each cluster gets its own spot on a wide ring, so they
    // start apart instead of untangling from one pile in the middle. A second hub starts by its first PR's.
    const cluster = (r: Row) => hubsOf(r, by)[0]?.join(':') ?? 'none'
    const clusters = [...new Set(rows.map(cluster))].sort()
    const ring = 90 * Math.sqrt(clusters.length)
    const spot = new Map(clusters.map((c, i) => {
      const a = (2 * Math.PI * i) / clusters.length
      return [c, { x: ring * Math.cos(a), y: ring * Math.sin(a) }]
    }))
    const home = new Map<string, { x: number; y: number }>()
    for (const r of rows) {
      const at = spot.get(cluster(r))!
      home.set(r.url, at)
      for (const h of hubsOf(r, by)) if (!home.has(h.join(':'))) home.set(h.join(':'), at)
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
        e.append('path').attr('class', 'person').attr('d', PERSON)
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
      // hubs push harder than PRs and the pull to the centre is gentle, so repo clusters sit apart
      .force('charge', forceManyBody<Node>().strength((n) => (n.kind === 'pr' ? -160 : -600)))
      .force('link', forceLink<Node, Link>(g.links).distance((l) => (l.primary && by === 'repo' ? 70 : l.primary ? 60 : 90)))
      .force('x', forceX(0).strength(0.025))
      .force('y', forceY(0).strength(0.025))
      .on('tick', () => {
        link
          .attr('x1', (l) => l.source.x!)
          .attr('y1', (l) => l.source.y!)
          .attr('x2', (l) => l.target.x!)
          .attr('y2', (l) => l.target.y!)
        node.attr('transform', (n) => `translate(${n.x},${n.y})`)
      })
    paint()
    // a regroup sends every PR to a new hub: full heat, or they stall halfway there
    s.alpha(old.size && lastBy.current === by ? 0.4 : 1).restart()
    lastBy.current = by

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
            placeholder="find by title, repo, author, kind"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
          />
        </div>
        <div className="vtabs">
          {GROUPS.map((g) => (
            <button key={g} className={by === g ? 'on' : ''} onClick={() => setBy(g)}>
              {g}
            </button>
          ))}
        </div>
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
        {by !== 'state' && (
          <span>
            <i style={{ background: 'var(--dim2)' }} /> {by === 'kind' ? 'kind' : 'repo'}
          </span>
        )}
        {by === 'author' && (
          <span>
            <svg className="gperson" viewBox="-1 -1 2 2">
              <path d={PERSON} />
            </svg>{' '}
            author
          </span>
        )}
        {rows.some((r) => tagOf(r).breaking) && (
          <span>
            <i className="gbreak" /> breaking
          </span>
        )}
        <span>{by === 'author' ? 'author & repo' : by} size = PRs · PR size = lines changed · scroll to zoom · drag to move</span>
      </div>
    </div>
  )
}
