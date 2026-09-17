import { useMemo, useState } from 'react'
import { chart, choices, GENERAL, type LEvent, MEASURES, MINE, type View } from '../learning'
import { Select } from './Controls'

/** A stack layer's colour, by its place in the legend: the largest part gets the first. Theme tokens, so every
 *  theme draws it in its own palette. */
const INK = ['var(--pink)', 'var(--cyan)', 'var(--violet)', 'var(--amber)', 'var(--green)', 'var(--red)', 'var(--dim2)']

const W = 860
const H = 220
const PAD = { l: 34, r: 8, t: 10, b: 24 }

const day = (s: number) => new Date(s * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

/** How fast the memory learns: one measure at a time, its breakdown stacked, filtered by team, repo and person.
 *  The counting is learning.ts's; this draws it. */
export function LearningChart({ events }: { events: LEvent[] }) {
  const [v, setV] = useState<View>({ measure: 'fact', team: '', repo: '', who: '', bucket: 'week' })
  const set = (part: Partial<View>) => setV((was) => ({ ...was, ...part }))
  const c = useMemo(() => chart(events, v), [events, v])
  const opts = useMemo(() => choices(events), [events])

  const n = c.buckets.length
  const top = Math.max(1, ...c.buckets.map((_, i) => c.parts.reduce((sum, p) => sum + p.counts[i], 0)))
  const slot = n ? (W - PAD.l - PAD.r) / n : 0
  const bar = Math.max(2, Math.min(28, slot * 0.7))
  const y = (count: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - count / top)
  // at most eight dates under the axis, so labels never run into each other
  const every = Math.max(1, Math.ceil(n / 8))

  return (
    <div className="learning">
      <div className="lbar">
        <div className="seg" role="group" aria-label="what to count">
          {MEASURES.map(([m, label]) => (
            <button key={m} aria-pressed={v.measure === m} onClick={() => set({ measure: m })}>
              {label}
            </button>
          ))}
        </div>
        <span className="sp" />
        <Select value={v.team} options={['', ...opts.teams]} show={(t) => (t ? t : 'every team')} onChange={(team) => set({ team })} />
        <Select value={v.repo} options={['', ...opts.repos]} show={(r) => (r ? r : 'every repo')} onChange={(repo) => set({ repo })} />
        <Select value={v.who} options={['', ...opts.people]} show={(w) => (w ? w : 'everyone')} onChange={(who) => set({ who })} />
        <div className="seg" role="group" aria-label="bucket">
          {(['day', 'week'] as const).map((b) => (
            <button key={b} aria-pressed={v.bucket === b} onClick={() => set({ bucket: b })}>
              {b}
            </button>
          ))}
        </div>
      </div>

      {c.total ? (
        <>
          <svg className="lchart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${c.total} ${v.measure}s per ${v.bucket}`}>
            {[top, Math.round(top / 2), 0].map((g) => (
              <g key={g}>
                <line x1={PAD.l} x2={W - PAD.r} y1={y(g)} y2={y(g)} className="grid" />
                <text x={PAD.l - 6} y={y(g) + 3} className="tick" textAnchor="end">
                  {g}
                </text>
              </g>
            ))}
            {c.buckets.map((b, i) => {
              const x = PAD.l + slot * i + (slot - bar) / 2
              let below = 0
              return (
                <g key={b}>
                  {c.parts.map((p, k) => {
                    const count = p.counts[i]
                    if (!count) return null
                    const r = (
                      <rect key={p.part} x={x} y={y(below + count)} width={bar} height={y(below) - y(below + count)} style={{ fill: INK[k % INK.length] }}>
                        <title>{`${v.bucket === 'week' ? 'week of ' : ''}${day(b)} · ${p.part}: ${count}`}</title>
                      </rect>
                    )
                    below += count
                    return r
                  })}
                  {i % every === 0 ? (
                    <text x={x + bar / 2} y={H - 6} className="tick" textAnchor="middle">
                      {day(b)}
                    </text>
                  ) : null}
                </g>
              )
            })}
          </svg>
          <div className="legend">
            {c.parts.map((p, k) => (
              <span key={p.part}>
                <i style={{ background: INK[k % INK.length] }} />
                {p.part} <b>{p.total}</b>
              </span>
            ))}
            <span className="sp" />
            <span className="dim">
              {c.total} in {n} {v.bucket}
              {n === 1 ? '' : 's'}
            </span>
          </div>
        </>
      ) : (
        <p className="empty">
          nothing {MEASURES.find(([m]) => m === v.measure)?.[1]} for this view
          {v.team === MINE || v.repo === GENERAL || v.team || v.repo || v.who ? ' — widen the filters' : ''}
        </p>
      )}
      <p className="note">
        Before the learning log began, counts come from the git history of your memory and each team, and how a fact
        was gained shows as <b>earlier</b>. Team facts are credited to the git author; arrivals to the name of the pool
        they landed in, so one person can appear under two names.
      </p>
    </div>
  )
}
