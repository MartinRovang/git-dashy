// The learning chart's arithmetic: which events a view shows, bucketed by day or week, and broken down the way
// the view breaks them down. The screen draws what this returns and decides nothing.

/** One thing learned, as /api/learning sends it. */
export type LEvent = {
  at: number
  kind: 'fact' | 'draft' | 'arrival'
  /** "owner/name", "" for general. */
  repo: string
  /** "" (absent) for your own memory. */
  team?: string
  who?: string
  source?: string
}

/** What the chart counts. Each has one breakdown, stacked in its bars. */
export type Measure = 'fact' | 'draft' | 'arrival'

export const MEASURES: [Measure, string][] = [
  ['fact', 'facts gained'],
  ['draft', 'drafts proposed'],
  ['arrival', 'team arrivals'],
]

/** "" means everything; MINE is your own memory, not any team's; GENERAL is facts true of no one repo. */
export const MINE = '(your memory)'
export const GENERAL = '(general)'

export type View = {
  measure: Measure
  team: string
  repo: string
  who: string
  bucket: 'day' | 'week'
}

const DAY = 86_400

/** The start of the day or the Monday-started week `at` falls in, in local time, as seconds. */
export function bucketOf(at: number, bucket: 'day' | 'week'): number {
  const d = new Date(at * 1000)
  d.setHours(0, 0, 0, 0)
  if (bucket === 'week') d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  return d.getTime() / 1000
}

/** How an event is broken down within its measure: a fact by how it was gained, a draft by where it came from,
 *  an arrival by whose it is. */
export function partOf(e: LEvent): string {
  if (e.kind === 'arrival') return e.who || 'someone'
  if (e.kind === 'fact') return e.source || 'earlier'
  return e.source || 'earlier'
}

/** Whether an event is in the view, measure aside. */
export function inView(e: LEvent, v: Omit<View, 'measure' | 'bucket'>): boolean {
  if (v.team === MINE ? !!e.team : v.team && e.team !== v.team) return false
  if (v.repo === GENERAL ? e.repo !== '' : v.repo && e.repo !== v.repo) return false
  if (v.who && (e.who || '') !== v.who) return false
  return true
}

export type Chart = {
  /** Bucket starts, oldest first, every bucket from the first event to the last, empty ones included. */
  buckets: number[]
  /** One stack layer per part, largest total first, each with a count per bucket. */
  parts: { part: string; counts: number[]; total: number }[]
  total: number
}

/** The chart for one view.
 *
 *  ponytail: empty buckets are drawn, not skipped. A week with nothing learned is the reading a rate chart exists
 *  for; skipping it closes the gap and makes a stall look like steady progress. */
export function chart(events: LEvent[], v: View): Chart {
  const shown = events.filter((e) => e.kind === v.measure && inView(e, v))
  if (!shown.length) return { buckets: [], parts: [], total: 0 }
  const first = bucketOf(Math.min(...shown.map((e) => e.at)), v.bucket)
  const last = bucketOf(Math.max(...shown.map((e) => e.at)), v.bucket)
  const buckets: number[] = []
  // step by calendar, not by a fixed 86400: a daylight-saving change makes one day 23 or 25 hours
  for (let b = first; b <= last; b = bucketOf(b + (v.bucket === 'week' ? 7 : 1) * DAY + DAY / 2, v.bucket)) buckets.push(b)
  const index = new Map(buckets.map((b, i) => [b, i]))
  const byPart = new Map<string, number[]>()
  for (const e of shown) {
    const p = partOf(e)
    const row = byPart.get(p) ?? Array(buckets.length).fill(0)
    row[index.get(bucketOf(e.at, v.bucket))!]++
    byPart.set(p, row)
  }
  const parts = [...byPart.entries()]
    .map(([part, counts]) => ({ part, counts, total: counts.reduce((a, b) => a + b, 0) }))
    .sort((a, b) => b.total - a.total || a.part.localeCompare(b.part))
  return { buckets, parts, total: shown.length }
}

/** What each filter can be set to, from the events themselves. */
export function choices(events: LEvent[]) {
  const sorted = (xs: Iterable<string>) => [...new Set(xs)].sort((a, b) => a.localeCompare(b))
  return {
    teams: [MINE, ...sorted(events.map((e) => e.team || '').filter(Boolean))],
    repos: [GENERAL, ...sorted(events.map((e) => e.repo).filter(Boolean))],
    people: sorted(events.map((e) => e.who || '').filter(Boolean)),
  }
}
