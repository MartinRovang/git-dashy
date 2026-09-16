// What a PR does to the database, as a live graph drawn the way the board's Graph is: the PR in the middle, the
// tables it touches around it with a glow per table, the columns it changes hanging off each, dashed foreign keys.
// Drag nodes, scroll to zoom, hover to light up neighbours. dbgraph.ts builds the nodes and links.
import { drag } from 'd3-drag'
import { forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force'
import { select } from 'd3-selection'
import { zoom, zoomIdentity } from 'd3-zoom'
import { useEffect, useMemo, useRef } from 'react'
import { build, CHANGE_TONE, type Link, type Node } from '../dbgraph'
import type { DbImpact } from '../types'

// a table, GitHub-octicon style: a frame, a header rule and a column rule, in a unit box centred on 0
const TABLE = 'M-.6,-.5H.6V.5H-.6ZM-.48,-.2H.48V.38H-.48ZM-.06,-.2H.06V.38H-.06Z'
const RADIUS = { pr: 12, table: 10, column: 4.5 }
const tone = (n: Node) => (n.kind === 'pr' ? 'var(--pink)' : CHANGE_TONE[n.change] || 'var(--dim)')
type Placed = Link & { source: Node; target: Node }

export function DbGraph({ db, number }: { db: DbImpact; number: number }) {
  const key = useMemo(() => JSON.stringify([db, number]), [db, number])
  const svgRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const { nodes, links: raw } = build(db, number)
    const root = select(svg)
    const world = root.select<SVGGElement>('g.world')
    const tables = nodes.filter((n) => n.kind === 'table')

    const sim = forceSimulation<Node>(nodes)
      .force('charge', forceManyBody<Node>().strength((n) => (n.kind === 'column' ? -60 : -420)).distanceMax(260))
      .force(
        'link',
        forceLink<Node, Link>(raw)
          .id((n) => n.id)
          .distance((l) => (l.kind === 'column' ? 30 : l.kind === 'ref' ? 110 : 95))
          .strength((l) => (l.kind === 'ref' ? 0.15 : 0.8)),
      )
      .force('collide', forceCollide<Node>((n) => RADIUS[n.kind] + 6))
    const links = raw as Placed[]

    // a soft glow behind each table and the columns hanging off it, in the table's tone
    const halo = world
      .select('g.halos')
      .selectAll<SVGCircleElement, Node>('circle')
      .data(tables, (t) => t.id)
      .join('circle')
      .style('fill', (t) => `url(#dbhalo-${t.change || 'none'})`)
    const link = world
      .select('g.links')
      .selectAll<SVGLineElement, Placed>('line')
      .data(links)
      .join('line')
      .classed('ref', (l) => l.kind === 'ref')
      .style('stroke', (l) => (l.kind === 'ref' ? null : tone(l.target)))
    const node = world
      .select('g.nodes')
      .selectAll<SVGGElement, Node>('g.gnode')
      .data(nodes, (n) => n.id)
      .join((enter) => {
        const e = enter.append('g').attr('class', 'gnode named')
        e.append('circle')
        e.append('path').attr('class', 'icon').attr('fill-rule', 'evenodd')
        e.append('text')
        e.append('title')
        return e
      })
      .classed('hub', (n) => n.kind === 'pr')
      .classed('dropped', (n) => n.change === 'dropped')
    node.select('title').text((n) => n.tip)
    node
      .select('text')
      .text((n) => (n.kind === 'pr' ? `#${number}` : n.label))
      .attr('y', (n) => RADIUS[n.kind] + 3)
      .style('fill', (n) => (n.kind === 'pr' ? 'var(--pink)' : null))
    node
      .select('circle')
      .attr('r', (n) => RADIUS[n.kind])
      .style('fill', (n) => (n.kind === 'column' ? tone(n) : `color-mix(in srgb, ${tone(n)} ${n.kind === 'pr' ? 55 : 30}%, var(--bg))`))
      .style('stroke', (n) => (n.kind === 'column' ? 'none' : tone(n)))
    node
      .select('path.icon')
      .attr('d', (n) => (n.kind === 'table' ? TABLE : null))
      .attr('transform', `scale(${RADIUS.table * 0.8})`)
      .style('fill', tone)

    // hover: the node and its neighbours stay lit, everything else fades
    const near = new Map<string, Set<string>>()
    const edge = (a: string, b: string) => (near.get(a) || near.set(a, new Set([a])).get(a)!).add(b)
    for (const l of links) {
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

    sim.on('tick', () => {
      halo.each(function (t) {
        const mine = nodes.filter((n) => n.table === t.label)
        const reach = Math.max(0, ...mine.map((n) => Math.hypot(n.x! - t.x!, n.y! - t.y!)))
        select(this).attr('cx', t.x!).attr('cy', t.y!).attr('r', reach + 34)
      })
      link
        .attr('x1', (l) => l.source.x!)
        .attr('y1', (l) => l.source.y!)
        .attr('x2', (l) => l.target.x!)
        .attr('y2', (l) => l.target.y!)
      node.attr('transform', (n) => `translate(${n.x},${n.y})`)
    })

    node.call(
      drag<SVGGElement, Node>()
        .on('start', (e, n) => {
          if (!e.active) sim.alphaTarget(0.3).restart()
          n.fx = n.x
          n.fy = n.y
        })
        .on('drag', (e, n) => {
          n.fx = e.x
          n.fy = e.y
        })
        .on('end', (e, n) => {
          if (!e.active) sim.alphaTarget(0)
          // the PR stays pinned in the middle; anything else is let go
          if (n.kind !== 'pr') {
            n.fx = null
            n.fy = null
          }
        }),
    )

    // zoom and pan; labels keep their size on screen, as on the board
    const fit = () => root.attr('viewBox', `${-svg.clientWidth / 2} ${-svg.clientHeight / 2} ${svg.clientWidth} ${svg.clientHeight}`)
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(svg)
    const z = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.4, 3])
      .on('zoom', ({ transform }) => {
        world.attr('transform', transform.toString())
        svg.style.setProperty('--k', String(transform.k))
      })
    root.call(z).on('dblclick.zoom', null)
    root.call(z.transform, zoomIdentity.scale(0.85))
    return () => {
      sim.stop()
      ro.disconnect()
      root.on('.zoom', null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return (
    <div className="graph dbg">
      <svg ref={svgRef} role="img" aria-label="tables this PR touches">
        <defs>
          {Object.entries(CHANGE_TONE).map(([c, t]) => (
            <radialGradient key={c} id={`dbhalo-${c}`}>
              <stop offset="0%" style={{ stopColor: t, stopOpacity: 0.28 }} />
              <stop offset="100%" style={{ stopColor: t, stopOpacity: 0 }} />
            </radialGradient>
          ))}
          <radialGradient id="dbhalo-none">
            <stop offset="0%" style={{ stopColor: 'var(--dim)', stopOpacity: 0.2 }} />
            <stop offset="100%" style={{ stopColor: 'var(--dim)', stopOpacity: 0 }} />
          </radialGradient>
        </defs>
        <g className="world">
          <g className="halos" />
          <g className="links" />
          <g className="nodes" />
        </g>
      </svg>
    </div>
  )
}
