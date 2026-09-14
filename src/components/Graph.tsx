// The whole board as a live graph, Obsidian/Quartz style. The group-by tab picks the galaxies: a named, coloured
// cloud per repo, author, kind (from its review) or review state. Inside a galaxy PRs hang off hubs, their repo,
// or their author in the repo tab. PRs are sized by lines changed and colored by review state.
// Drag nodes, scroll to zoom, hover to light up neighbours.
//
// ponytail: d3-force + SVG, not Quartz's PixiJS canvas. A board is tens of PRs, not thousands of
// notes; switch the drawing to canvas if a board ever gets big enough for SVG to stutter.
import { drag } from 'd3-drag'
import type { Simulation, SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import { forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force'
import { select } from 'd3-selection'
import { zoom, zoomIdentity } from 'd3-zoom'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { VisSection } from '../board'
import { avatar, PALETTE, rowState } from '../tokens'
import type { Row } from '../types'

type Hub = 'repo' | 'author'
type Node = SimulationNodeDatum & { id: string; kind: 'pr' | Hub; label: string; degree: number; galaxy: string }
type Link = SimulationLinkDatum<Node> & { source: Node; target: Node }
const GROUPS = ['repo', 'kind', 'author', 'state'] as const
type Group = (typeof GROUPS)[number]

// most PR titles drawn at once: past this many on screen they are unreadable and SVG text is what makes WebKit crawl
const LABELS = 120
/** The zoom the graph opens at: below 0.8, where titles start, so opening it draws no text. */
const START = 0.6
// clear space kept between two galaxies' outermost nodes; the halos pad 50 on each side, so their glows just meet
const GAP = 100

const lines = (r?: Row) => (r?.add ?? 0) + (r?.del ?? 0)
// a person, head and shoulders, in a unit box centred on 0: author nodes scale it to their radius
const PERSON = 'M-.3,-.32a.3,.3 0 1,0 .6,0a.3,.3 0 1,0 -.6,0ZM-.62,.62Q-.62,.06 0,.06Q.62,.06 .62,.62Z'
// a closed book, GitHub's repo glyph, same box; evenodd cuts the spine and the page edge out of the cover
const BOOK = 'M-.5,-.6H.5V.6H-.5ZM-.3,-.48H-.22V.22H-.3ZM-.3,.34H.38V.46H-.3Z'
const ICON: Record<Hub, string> = { author: PERSON, repo: BOOK }

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

/** The galaxy a PR sits in: whatever the tab groups by. */
function galaxyOf(r: Row, by: Group): string {
  if (by === 'repo') return r.repo
  if (by === 'author') return r.author || 'no author'
  if (by === 'kind') return tagOf(r).kind
  return rowState(r).key
}

/** The hub a PR hangs off inside its galaxy, one per galaxy: its author in the repo tab, its repo anywhere else. */
function hubOf(r: Row, by: Group): { kind: Hub; label: string; id: string } | null {
  // an author whose account is gone has no login; no hub rather than pool them all on a blank one
  if (by === 'repo' && !r.author) return null
  const [kind, label]: [Hub, string] = by === 'repo' ? ['author', r.author] : ['repo', r.repo]
  return { kind, label, id: `${kind}:${galaxyOf(r, by)}|${label}` }
}

function build(rows: Row[], by: Group): { nodes: Node[]; links: Link[] } {
  const hubs = new Map<string, Node>()
  const nodes: Node[] = []
  const links: Link[] = []
  for (const r of rows) {
    // the title labels a PR; repo and number are in the tooltip
    const label = r.title.length > 40 ? r.title.slice(0, 39) + '…' : r.title
    const galaxy = galaxyOf(r, by)
    const pr: Node = { id: r.url, kind: 'pr', label, degree: 2, galaxy }
    nodes.push(pr)
    const hub = hubOf(r, by)
    if (!hub) continue
    let h = hubs.get(hub.id)
    if (!h) {
      h = { ...hub, degree: 0, galaxy }
      hubs.set(hub.id, h)
      nodes.push(h)
    }
    h.degree++
    links.push({ source: pr, target: h })
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
  const key = useMemo(() => by + '|' + rows.map((r) => `${r.url}\t${r.title}\t${galaxyOf(r, by)}\t${hubOf(r, by)?.id}`).sort().join('|'), [rows, by])
  const lastBy = useRef(by)
  const svgRef = useRef<SVGSVGElement>(null)
  const sim = useRef<Simulation<Node, Link> | null>(null)
  const graph = useRef<{ nodes: Node[]; links: Link[] }>({ nodes: [], links: [] })
  // highlights rather than filters: dropping nodes would re-run the layout on every keystroke
  const [query, setQuery] = useState('')
  const view = useRef(zoomIdentity)
  const capped = useRef(false)
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
    // same fields as the board's filter box; a hit lights its PR, that PR's hub and its galaxy
    const q = query.trim().toLowerCase()
    const hits = new Set<string>()
    const lit = new Set<string>()
    for (const r of rows) {
      if (!q || !`${r.title} ${r.repo} ${r.author} #${r.number} ${tagOf(r).kind}`.toLowerCase().includes(q)) continue
      hits.add(r.url)
      lit.add(galaxyOf(r, latest.current.by))
      const hub = hubOf(r, latest.current.by)
      if (hub) hits.add(hub.id)
    }
    select(svg).classed('search', !!q)
    select(svg).selectAll<SVGElement, string>('.halos circle, .gnames text').classed('hit', (d) => lit.has(d))
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
      .select('path.icon')
      .attr('d', (n) => (n.kind === 'pr' ? null : ICON[n.kind]))
      .attr('transform', (n) => `scale(${radius(n) * (n.kind === 'repo' ? 0.6 : 0.75)})`)
      .style('fill', (n) => (n.kind === 'author' ? avatar(n.label) : 'var(--ink3)'))
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
        const c = n.kind === 'author' ? avatar(n.label) : 'var(--dim2)'
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

  // Titles only for nodes on screen, and PR titles only while few enough are. Runs every tick, so it touches
  // the DOM only for nodes whose answer changed.
  const cull = () => {
    const svg = svgRef.current
    if (!svg) return
    const { k, x, y } = view.current
    const w = svg.clientWidth / 2
    const h = svg.clientHeight / 2
    // padded: a title hangs below its node, so it stays while the node is just past the edge
    const shown = (n: Node) => Math.abs(n.x! * k + x) < w + 40 && Math.abs(n.y! * k + y) < h + 40
    const prs = graph.current.nodes.filter((n) => n.kind === 'pr' && shown(n)).length
    // hysteresis: a count hovering at LABELS while the layout settles would flip every title on and off together
    if (prs > LABELS) capped.current = true
    else if (prs < LABELS * 0.8) capped.current = false
    select(svg)
      .selectAll<SVGGElement, Node>('g.gnode')
      .each(function (n) {
        // at k <= .8 --label hides every title anyway
        const named = k > 0.8 && shown(n) && (n.kind !== 'pr' || !capped.current)
        if (named !== this.classList.contains('named')) this.classList.toggle('named', named)
      })
  }

  // Structure: rebuilt when the set of PRs changes. Nodes that survive keep their position.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const old = new Map(graph.current.nodes.map((n) => [n.id, n]))
    const { rows, by } = latest.current
    const g = build(rows, by)
    // A new node spawns where its galaxy already is, or for a new galaxy (or a regroup) at its own spot on a ring,
    // so galaxies start apart instead of untangling from one pile in the middle. The galaxy force below does the rest.
    const galaxies = [...new Set(rows.map((r) => galaxyOf(r, by)))].sort()
    // a lone galaxy sits in the middle
    const ring = galaxies.length > 1 ? 90 * Math.sqrt(galaxies.length) : 0
    const spot = new Map(galaxies.map((c, i) => {
      const a = (2 * Math.PI * i) / galaxies.length
      const mates = lastBy.current === by ? [...old.values()].filter((n) => n.galaxy === c) : []
      if (!mates.length) return [c, { x: ring * Math.cos(a), y: ring * Math.sin(a) }]
      return [c, { x: mates.reduce((t, n) => t + n.x!, 0) / mates.length, y: mates.reduce((t, n) => t + n.y!, 0) / mates.length }]
    }))
    for (const n of g.nodes) {
      const was = old.get(n.id)
      const at = spot.get(n.galaxy)!
      if (was) Object.assign(n, { x: was.x, y: was.y, vx: was.vx, vy: was.vy })
      else Object.assign(n, { x: at.x + Math.random() * 30 - 15, y: at.y + Math.random() * 30 - 15 })
    }
    graph.current = g

    const root = select(svg)
    const world = root.select<SVGGElement>('g.world')
    // a soft glow behind each galaxy. A radial gradient per galaxy fades each disc out;
    // an SVG blur filter looked the same but re-rasterised on every tick and made the layout crawl
    const gid = (d: string) => `ghalo-${galaxies.indexOf(d)}`
    // no two galaxies share a colour: hues spread evenly round the wheel, one slice each.
    // The ring order steps through them by a stride coprime to the count, so ring neighbours usually sit apart in hue too
    const count = galaxies.length
    const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a)
    let stride = Math.max(1, Math.round(count * 0.38))
    while (gcd(stride, count) !== 1) stride++
    const tint = (d: string, light: number) => `hsl(${(((galaxies.indexOf(d) * stride) % count) * 360) / count} 90% ${light}%)`
    root
      .select('defs')
      .selectAll<SVGRadialGradientElement, string>('radialGradient')
      .data(galaxies, (d) => d)
      .join((enter) => {
        const e = enter.append('radialGradient')
        e.append('stop').attr('offset', '0%').style('stop-color', 'currentColor').style('stop-opacity', 0.35)
        e.append('stop').attr('offset', '100%').style('stop-color', 'currentColor').style('stop-opacity', 0)
        return e
      })
      .attr('id', gid)
      // the stops paint currentColor
      .style('color', (d) => tint(d, 55))
    const halo = world
      .select('g.halos')
      .selectAll<SVGCircleElement, string>('circle')
      .data(galaxies, (d) => d)
      .join('circle')
      .style('fill', (d) => `url(#${gid(d)})`)
    // the names get their own layer above the nodes, so a node never covers one
    const gname = world
      .select('g.gnames')
      .selectAll<SVGTextElement, string>('text')
      .data(galaxies, (d) => d)
      .join('text')
      .text((d) => d)
      .style('fill', (d) => tint(d, 65))
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
        e.append('path').attr('class', 'icon').attr('fill-rule', 'evenodd')
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

    // galaxy membership is fixed until the next rebuild; the tick only moves the centroids
    const area = new Map<string, Node[]>()
    for (const n of g.nodes) (area.get(n.galaxy) || area.set(n.galaxy, []).get(n.galaxy)!).push(n)
    // each galaxy's centre and reach (its farthest node), measured every tick; the halos draw it
    let at = new Map<string, { cx: number; cy: number; r: number }>()
    // A force per galaxy: its nodes are drawn to its centre, the whole board drifts to the middle, and two galaxies
    // closer than their reach plus GAP are pushed apart. The push is not cooled by alpha, like forceCollide,
    // so when the layout settles no two galaxies overlap.
    // ponytail: O(galaxies²) per tick, fine for tens of galaxies; a quadtree if a board ever has hundreds
    const pull = (alpha: number) => {
      at = new Map(galaxies.map((d) => {
        const ns = area.get(d) ?? []
        const cx = ns.reduce((a, n) => a + n.x!, 0) / ns.length
        const cy = ns.reduce((a, n) => a + n.y!, 0) / ns.length
        return [d, { cx, cy, r: Math.max(0, ...ns.map((n) => Math.hypot(n.x! - cx, n.y! - cy))) }]
      }))
      const push = new Map(galaxies.map((d) => [d, { x: 0, y: 0 }]))
      for (let i = 0; i < galaxies.length; i++) {
        for (let j = i + 1; j < galaxies.length; j++) {
          const a = at.get(galaxies[i])!
          const b = at.get(galaxies[j])!
          const dx = b.cx - a.cx
          const dy = b.cy - a.cy
          const d = Math.hypot(dx, dy) || 1
          const over = a.r + b.r + GAP - d
          if (over <= 0) continue
          const pa = push.get(galaxies[i])!
          const pb = push.get(galaxies[j])!
          pa.x -= (dx / d) * over * 0.1
          pa.y -= (dy / d) * over * 0.1
          pb.x += (dx / d) * over * 0.1
          pb.y += (dy / d) * over * 0.1
        }
      }
      for (const n of g.nodes) {
        const c = at.get(n.galaxy)!
        const p = push.get(n.galaxy)!
        n.vx! += p.x + ((c.cx - n.x!) * 0.08 - n.x! * 0.02) * alpha
        n.vy! += p.y + ((c.cy - n.y!) * 0.08 - n.y! * 0.02) * alpha
      }
    }
    const s = (sim.current ||= forceSimulation<Node, Link>())
    s.nodes(g.nodes)
      // hubs push harder than PRs, so the hubs inside a galaxy spread out around its spot
      // short range: inside a galaxy only; the galaxy force keeps galaxies apart, a long reach would fling big ones far
      .force('charge', forceManyBody<Node>().strength((n) => (n.kind === 'pr' ? -160 : -600)).distanceMax(300))
      .force('link', forceLink<Node, Link>(g.links).distance(60))
      .force('galaxy', pull)
      .on('tick', () => {
        halo.attr('cx', (d) => at.get(d)!.cx).attr('cy', (d) => at.get(d)!.cy).attr('r', (d) => at.get(d)!.r + 50)
        gname.attr('x', (d) => at.get(d)!.cx).attr('y', (d) => at.get(d)!.cy - (at.get(d)!.r + 50) * 0.6)
        link
          .attr('x1', (l) => l.source.x!)
          .attr('y1', (l) => l.source.y!)
          .attr('x2', (l) => l.target.x!)
          .attr('y2', (l) => l.target.y!)
        node.attr('transform', (n) => `translate(${n.x},${n.y})`)
        cull()
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

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(paint, [rows, sel, query, by])

  // Zoom and pan, once. Labels fade in as you zoom, like Quartz, so a wide view stays uncluttered.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const root = select(svg)
    const fit = () => {
      root.attr('viewBox', `${-svg.clientWidth / 2} ${-svg.clientHeight / 2} ${svg.clientWidth} ${svg.clientHeight}`)
      cull()
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(svg)
    const z = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.25, 4])
      .on('zoom', ({ transform }) => {
        root.select('g.world').attr('transform', transform.toString())
        view.current = transform
        cull()
        svg.style.setProperty('--label', String(Math.min(1, Math.max(0, (transform.k - 0.8) * 2))))
        svg.style.setProperty('--k', String(transform.k))
      })
    root.call(z).on('dblclick.zoom', null)
    // ponytail: opens zoomed out past where titles draw (k <= .8), so the first frames lay out dots, not
    // hundreds of text nodes; scroll in to read. Through z.transform, so view, cull and --label all follow.
    root.call(z.transform, zoomIdentity.scale(START))
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
        <defs />
        <g className="world">
          <g className="halos" />
          <g className="links" />
          <g className="nodes" />
          <g className="gnames" />
        </g>
      </svg>
      <div className="glegend">
        {states.map((k) => (
          <span key={k}>
            <i style={{ background: PALETTE[k].fg }} />
            {k}
          </span>
        ))}
        {by !== 'repo' && (
          <span>
            <svg className="gicon" viewBox="-1 -1 2 2">
              <path d={BOOK} fillRule="evenodd" />
            </svg>{' '}
            repo
          </span>
        )}
        {by === 'repo' && (
          <span>
            <svg className="gicon" viewBox="-1 -1 2 2">
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
        <span>cloud = {by} · hub size = PRs · PR size = lines changed · scroll to zoom · drag to move</span>
      </div>
    </div>
  )
}
