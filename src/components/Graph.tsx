// The whole board as one picture: PRs are nodes sized by how much they change, and drawn near the
// PRs they share a repo with (brighter when they also share an author). Hand-rolled force layout —
// no graph library — so a poll that only changes a size never moves anything.
import { useMemo } from 'react'
import type { VisSection } from '../board'
import { PALETTE, rowState } from '../tokens'
import type { Row, Size } from '../types'

const W = 1000
const H = 680
const PAD = 42
const R0 = 5
const RMAX = 23

type Edge = [number, number, boolean] // a, b, same author

/** Fruchterman-Reingold, deterministic, ~260 ticks. Membership, not sizes, decides the positions. */
function layout(n: number, edges: Edge[]): { x: number; y: number }[] {
  const pos = Array.from({ length: n }, (_, i) => {
    const a = i * 2.399963
    const rad = 15 * Math.sqrt(i + 1)
    return { x: W / 2 + rad * Math.cos(a), y: H / 2 + rad * Math.sin(a) }
  })
  if (n < 2) return pos
  const k = Math.sqrt((W * H) / n) * 0.8
  for (let it = 0; it < 260; it++) {
    const disp = pos.map(() => ({ x: 0, y: 0 }))
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i].x - pos[j].x
        const dy = pos[i].y - pos[j].y
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01
        const f = (k * k) / (d * d)
        const fx = (dx / d) * f
        const fy = (dy / d) * f
        disp[i].x += fx
        disp[i].y += fy
        disp[j].x -= fx
        disp[j].y -= fy
      }
    for (const [a, b, same] of edges) {
      const dx = pos[a].x - pos[b].x
      const dy = pos[a].y - pos[b].y
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01
      const f = ((d * d) / k) * (same ? 1.3 : 0.85)
      const fx = (dx / d) * f
      const fy = (dy / d) * f
      disp[a].x -= fx
      disp[a].y -= fy
      disp[b].x += fx
      disp[b].y += fy
    }
    const t = k * (1 - it / 260) * 0.55 + 1
    for (let i = 0; i < n; i++) {
      disp[i].x += (W / 2 - pos[i].x) * 0.012
      disp[i].y += (H / 2 - pos[i].y) * 0.012
      const d = Math.sqrt(disp[i].x ** 2 + disp[i].y ** 2) || 0.01
      const step = Math.min(d, t)
      pos[i].x += (disp[i].x / d) * step
      pos[i].y += (disp[i].y / d) * step
    }
  }
  return pos
}

export function Graph({ secs, sizes, measuring, sel, onSelect }: {
  secs: VisSection[]
  sizes: Record<string, Size>
  measuring: boolean
  sel: string
  onSelect: (uid: string) => void
}) {
  const rows = useMemo(() => secs.flatMap((s) => s.prs), [secs])
  const key = rows.map((r) => r.url).sort().join('|')
  const maxLines = Math.max(1, ...rows.map((r) => (sizes[r.url]?.add || 0) + (sizes[r.url]?.del || 0)))

  const { pos, edges, repos } = useMemo(() => {
    const edges: Edge[] = []
    const byRepo = new Map<string, number[]>()
    rows.forEach((r, i) => {
      const g = byRepo.get(r.repo) || []
      g.push(i)
      byRepo.set(r.repo, g)
    })
    const repos: { name: string; x: number; y: number }[] = []
    for (const [name, idx] of byRepo) {
      for (let a = 0; a < idx.length; a++)
        for (let b = a + 1; b < idx.length; b++) edges.push([idx[a], idx[b], rows[idx[a]].author === rows[idx[b]].author])
      repos.push({ name, x: 0, y: 0 })
    }
    const pos = layout(rows.length, edges)
    for (const [name, idx] of byRepo) {
      const i = repos.findIndex((r) => r.name === name)
      repos[i].x = idx.reduce((s, j) => s + pos[j].x, 0) / idx.length
      repos[i].y = idx.reduce((s, j) => s + pos[j].y, 0) / idx.length
    }
    return { pos, edges, repos }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (!rows.length) return <div className="graph empty">nothing to graph yet</div>

  const bounds = () => {
    const xs = pos.map((p) => p.x)
    const ys = pos.map((p) => p.y)
    const pad = PAD + RMAX
    const x0 = Math.min(...xs) - pad
    const y0 = Math.min(...ys) - pad
    return { x0, y0, w: Math.max(...xs) - x0 + pad, h: Math.max(...ys) - y0 + pad }
  }
  const b = bounds()
  const radius = (r: Row) => {
    const s = sizes[r.url]
    if (!s || s.add == null || s.del == null) return R0
    const lines = s.add + s.del
    return Math.max(R0, R0 + (RMAX - R0) * Math.sqrt(lines / maxLines))
  }
  const measured = rows.filter((r) => sizes[r.url]?.add != null).length
  return (
    <div className="graph">
      <div className="gbadge">
        {measuring ? (
          <span>
            <i className="spinner" /> measuring diffs {measured}/{rows.length}
          </span>
        ) : (
          <span>
            {rows.length} PRs · {new Set(rows.map((r) => r.repo)).size} repos
          </span>
        )}
      </div>
      <svg viewBox={`${b.x0} ${b.y0} ${b.w} ${b.h}`} preserveAspectRatio="xMidYMid meet">
        {edges.map(([a, e, same], i) => (
          <line
            key={i}
            x1={pos[a].x}
            y1={pos[a].y}
            x2={pos[e].x}
            y2={pos[e].y}
            stroke={same ? 'var(--violet)' : 'var(--line2)'}
            strokeWidth={same ? 1.6 : 1}
            opacity={same ? 0.5 : 0.7}
          />
        ))}
        {repos.map((r) => (
          <text key={r.name} x={r.x} y={r.y} className="grepo">
            {r.name}
          </text>
        ))}
        {rows.map((r, i) => {
          const st = rowState(r)
          const pal = PALETTE[st.key] || PALETTE.idle
          const rr = radius(r)
          const known = sizes[r.url]?.add != null
          return (
            <g key={r.url} className="gnode" transform={`translate(${pos[i].x} ${pos[i].y})`} onClick={() => onSelect(r.uid)}>
              <title>
                #{r.number} {r.title}
                {'\n'}
                {r.repo} · {r.author}
                {'\n'}
                {known ? `+${sizes[r.url].add} −${sizes[r.url].del}` : 'measuring…'}
              </title>
              {r.uid === sel ? <circle r={rr + 4} className="gsel" /> : null}
              <circle r={rr} fill={known ? pal.bg : 'transparent'} stroke={pal.border} strokeWidth={1.5} strokeDasharray={known ? undefined : '2 2'} />
              {rr >= 9 ? (
                <text y={rr + 11} className="glabel">
                  #{r.number}
                </text>
              ) : null}
            </g>
          )
        })}
      </svg>
      <div className="glegend">
        {(['approved', 'changes', 'commented', 'awaiting', 'running', 'error', 'idle'] as const)
          .filter((k) => rows.some((r) => rowState(r).key === k))
          .map((k) => (
            <span key={k}>
              <i style={{ background: PALETTE[k].border }} />
              {k}
            </span>
          ))}
        <span className="gsize">
          <i className="gdot" style={{ width: 6, height: 6 }} />
          <i className="gdot" style={{ width: 11, height: 11 }} />
          <i className="gdot" style={{ width: 16, height: 16 }} />
          diff size
        </span>
        <span>
          <i style={{ background: 'var(--violet)' }} /> same author
        </span>
      </div>
    </div>
  )
}
