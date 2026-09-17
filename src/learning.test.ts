import { describe, expect, it } from 'vitest'
import { bucketOf, chart, choices, GENERAL, inView, type LEvent, MINE, partOf, type View } from './learning'

// a local-time date, so the tests mean the same thing in any timezone
const at = (y: number, m: number, d: number, h = 12) => new Date(y, m - 1, d, h).getTime() / 1000
const ev = (over: Partial<LEvent>): LEvent => ({ at: at(2026, 9, 14), kind: 'draft', repo: 'acme/api', ...over })
const view = (over: Partial<View> = {}): View => ({ measure: 'draft', team: '', repo: '', who: '', bucket: 'day', ...over })

describe('buckets', () => {
  it('puts a moment in its day and in its Monday-started week', () => {
    // Wednesday 16 September 2026, late in the evening
    expect(bucketOf(at(2026, 9, 16, 23), 'day')).toBe(at(2026, 9, 16, 0))
    expect(bucketOf(at(2026, 9, 16, 23), 'week')).toBe(at(2026, 9, 14, 0))
    // a Sunday belongs to the week that started the Monday before, not the next one
    expect(bucketOf(at(2026, 9, 20, 9), 'week')).toBe(at(2026, 9, 14, 0))
  })
})

describe('the chart', () => {
  it('draws a bucket with nothing in it, so a stall shows as one', () => {
    const c = chart([ev({ at: at(2026, 9, 14) }), ev({ at: at(2026, 9, 16) })], view())
    expect(c.buckets).toEqual([at(2026, 9, 14, 0), at(2026, 9, 15, 0), at(2026, 9, 16, 0)])
    expect(c.parts[0].counts).toEqual([1, 0, 1])
  })

  it('counts only the measure shown, broken down the way that measure is', () => {
    const events = [
      ev({ kind: 'fact', source: 'seen twice' }),
      ev({ kind: 'fact', source: 'seen twice' }),
      ev({ kind: 'fact', source: 'hand' }),
      ev({ kind: 'fact' }), // from git, before the log: how is not known
      ev({ kind: 'draft', source: 'review' }),
    ]
    const c = chart(events, view({ measure: 'fact' }))
    expect(c.total).toBe(4)
    expect(c.parts.map((p) => [p.part, p.total])).toEqual([
      ['seen twice', 2],
      ['earlier', 1],
      ['hand', 1],
    ])
  })

  it('breaks arrivals down by person', () => {
    const c = chart([ev({ kind: 'arrival', team: 't', who: 'martin' }), ev({ kind: 'arrival', team: 't', who: 'pontus' }), ev({ kind: 'arrival', team: 't', who: 'martin' })], view({ measure: 'arrival' }))
    expect(c.parts.map((p) => p.part)).toEqual(['martin', 'pontus'])
  })

  it('is empty, not broken, when nothing is in view', () => {
    expect(chart([], view())).toEqual({ buckets: [], parts: [], total: 0 })
    expect(chart([ev({ kind: 'fact' })], view({ measure: 'arrival' })).total).toBe(0)
  })

  it('does not lose a day across a daylight-saving change', () => {
    // the last Sunday of October: that day is 25 hours long in most of Europe, and a fixed step skips or repeats
    const c = chart([ev({ at: at(2026, 10, 24) }), ev({ at: at(2026, 10, 27) })], view())
    expect(c.buckets.map((b) => new Date(b * 1000).getDate())).toEqual([24, 25, 26, 27])
  })
})

describe('filters', () => {
  it('tells your memory from a team, and general facts from a repo', () => {
    const mine = ev({})
    const team = ev({ team: 'teamdashy' })
    const general = ev({ repo: '' })
    expect([inView(mine, { team: MINE, repo: '', who: '' }), inView(team, { team: MINE, repo: '', who: '' })]).toEqual([true, false])
    expect(inView(team, { team: 'teamdashy', repo: '', who: '' })).toBe(true)
    expect([inView(general, { team: '', repo: GENERAL, who: '' }), inView(mine, { team: '', repo: GENERAL, who: '' })]).toEqual([true, false])
    expect(inView(mine, { team: '', repo: 'acme/web', who: '' })).toBe(false)
    expect(inView(ev({ who: 'martin' }), { team: '', repo: '', who: 'pontus' })).toBe(false)
  })

  it('offers what the events hold, and your memory and general always', () => {
    const c = choices([ev({ team: 'b', who: 'martin' }), ev({ team: 'a', repo: '', who: 'pontus' }), ev({})])
    expect(c.teams).toEqual([MINE, 'a', 'b'])
    expect(c.repos).toEqual([GENERAL, 'acme/api'])
    expect(c.people).toEqual(['martin', 'pontus'])
  })

  it('names an unattributed part rather than leaving it blank', () => {
    expect([partOf(ev({ kind: 'arrival' })), partOf(ev({ kind: 'draft' }))]).toEqual(['someone', 'earlier'])
  })
})
