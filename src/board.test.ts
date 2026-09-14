import { describe, expect, it } from 'vitest'
import type { Pr, Section, StateData } from './types'
import { ALL, buckets, flat, inBucket, pickBucket, selected, visible } from './board'

let n = 0

// A PR with every field the filters read, so a test can change one and mean it. The url counts up
// rather than being random, so two boards built the same way really are equal.
function pr(over: Partial<Pr> = {}): Pr {
  return {
    url: `https://x/${++n}`,
    number: 1,
    title: 'a title',
    repo: 'acme/web',
    author: 'bob',
    updatedAt: '2026-09-14T08:00:00Z',
    isDraft: false,
    status: '',
    prev: '',
    checks: 'ci✓',
    reviewers: '',
    review: '',
    busy: false,
    team: '',
    summary: '',
    reviewAt: '2026-09-14T08:00:00Z',
    kind: '',
    breaking: false,
    pre: null,
    ...over,
  }
}

function state(sections: Partial<Section>[], settings: Record<string, unknown> = {}): StateData {
  return {
    version: '0',
    sections: sections.map((s) => ({ name: 'MINE', prs: [], error: '', ...s })),
    fetchedAt: 1,
    interval: 60,
    fetching: false,
    error: '',
    auto: false,
    pending: 0,
    model: 'opus',
    running: 0,
    update: '',
    // drafts on by default so a test that is not about drafts is not silently filtered
    settings: { drafts: true, ...settings },
    options: { model: [], depth: [], effort: [], voice: [], hunter: [], subs: [], window: [], interval: [], theme: [] },
    knowledge: { memory: '', store: '', teams: [], teamError: '', notes: [] },
    asks: [],
    notices: [],
  }
}

const secs = (d: StateData) => visible(d, '', false, false)

describe('buckets', () => {
  it('puts All in front and its count is every section summed', () => {
    const v = secs(state([
      { name: 'MINE', prs: [pr(), pr()] },
      { name: 'ASSIGNED', prs: [pr()] },
    ]))
    expect(buckets(v).map((b) => [b.key, b.n])).toEqual([
      [ALL, 3],
      ['MINE', 2],
      ['ASSIGNED', 1],
    ])
  })

  it('labels a bucket with the section name lowercased', () => {
    const v = secs(state([{ name: 'REVIEW REQUESTED', prs: [] }]))
    expect(buckets(v)[1].label).toBe('review requested')
  })

  it('keeps a section with no PRs, so a tab does not vanish when a filter empties it', () => {
    const v = visible(state([{ name: 'MINE', prs: [pr({ title: 'keep' })] }]), 'nothing matches', false, false)
    expect(buckets(v).map((b) => b.key)).toEqual([ALL, 'MINE'])
    expect(buckets(v)[1].n).toBe(0)
  })
})

describe('inBucket', () => {
  const v = () =>
    secs(state([
      { name: 'MINE', prs: [pr()] },
      { name: 'ASSIGNED', prs: [pr(), pr()] },
    ]))

  it('ALL is every section', () => {
    expect(inBucket(v(), [ALL]).map((s) => s.name)).toEqual(['MINE', 'ASSIGNED'])
  })

  it('a named bucket is that section alone', () => {
    expect(inBucket(v(), ['ASSIGNED']).map((s) => s.name)).toEqual(['ASSIGNED'])
  })

  // the documented anti-fallback: a bucket the server no longer sends must NOT widen to everything
  it('an unknown bucket yields nothing rather than falling back to ALL', () => {
    expect(inBucket(v(), ['RETIRED'])).toEqual([])
  })

  it('holds more than one queue at a time', () => {
    expect(inBucket(v(), ['ASSIGNED', 'MINE']).map((s) => s.name)).toEqual(['MINE', 'ASSIGNED'])
  })

  // the order is the server's, so adding a tab never reshuffles the rows already on screen
  it('keeps section order whatever order the tabs were picked in', () => {
    const board = v()
    expect(inBucket(board, ['ASSIGNED', 'MINE'])).toEqual(inBucket(board, ['MINE', 'ASSIGNED']))
  })

  it('ALL alongside a section still means every section', () => {
    expect(inBucket(v(), [ALL, 'MINE']).map((s) => s.name)).toEqual(['MINE', 'ASSIGNED'])
  })

  it('an empty pick is every section, not none', () => {
    expect(inBucket(v(), []).map((s) => s.name)).toEqual(['MINE', 'ASSIGNED'])
  })

  it('an unknown name alongside a real one contributes nothing', () => {
    expect(inBucket(v(), ['RETIRED', 'MINE']).map((s) => s.name)).toEqual(['MINE'])
  })
})

describe('pickBucket', () => {
  it('All replaces whatever was picked', () => {
    expect(pickBucket(['MINE', 'ASSIGNED'], ALL)).toEqual([ALL])
  })

  it('a section clicked from All replaces All rather than joining it', () => {
    expect(pickBucket([ALL], 'MINE')).toEqual(['MINE'])
  })

  it('a second section stacks onto the first', () => {
    expect(pickBucket(['MINE'], 'ASSIGNED')).toEqual(['MINE', 'ASSIGNED'])
  })

  it('clicking a picked section takes it out', () => {
    expect(pickBucket(['MINE', 'ASSIGNED'], 'MINE')).toEqual(['ASSIGNED'])
  })

  it('taking the last one out falls back to All rather than leaving an empty board', () => {
    expect(pickBucket(['MINE'], 'MINE')).toEqual([ALL])
  })

  it('never repeats a section', () => {
    expect(pickBucket(['MINE'], 'MINE').filter((b) => b === 'MINE')).toEqual([])
    expect(pickBucket(['MINE', 'ASSIGNED'], 'ASSIGNED')).toEqual(['MINE'])
  })
})

describe('flat', () => {
  it('walks the bucket in section order', () => {
    const v = secs(state([
      { name: 'MINE', prs: [pr({ title: 'a' })] },
      { name: 'ASSIGNED', prs: [pr({ title: 'b' }), pr({ title: 'c' })] },
    ]))
    expect(flat(v, [ALL], {}).map((r) => r.title)).toEqual(['a', 'b', 'c'])
    expect(flat(v, ['ASSIGNED'], {}).map((r) => r.title)).toEqual(['b', 'c'])
  })

  it('expands a row’s older runs only when that row is expanded', () => {
    const url = 'https://x/42'
    const d = state([{ name: 'REVIEWED', prs: [pr({ url, title: 'new' }), pr({ url, title: 'old' })] }])
    const v = secs(d)
    expect(flat(v, [ALL], {}).map((r) => r.title)).toEqual(['new'])
    expect(flat(v, [ALL], { [url]: true }).map((r) => r.title)).toEqual(['new', 'old'])
  })

  it('still expands older runs inside a named bucket, not only in ALL', () => {
    const url = 'https://x/7'
    const v = secs(state([
      { name: 'MINE', prs: [pr()] },
      { name: 'REVIEWED', prs: [pr({ url, title: 'new' }), pr({ url, title: 'old' })] },
    ]))
    expect(flat(v, ['REVIEWED'], { [url]: true }).map((r) => r.title)).toEqual(['new', 'old'])
  })
})

describe('visible: the drafts rules compose', () => {
  const board = (settings: Record<string, unknown> = {}) =>
    state(
      [
        {
          name: 'MINE',
          prs: [
            pr({ title: 'plain' }),
            pr({ title: 'draft' , isDraft: true }),
            pr({ title: 'draft busy', isDraft: true, busy: true }),
            pr({ title: 'draft reviewed', isDraft: true, review: '✓ looks fine' }),
          ],
        },
      ],
      settings,
    )
  const titles = (d: StateData, onlyDrafts: boolean) => visible(d, '', false, onlyDrafts)[0].prs.map((p) => p.title)

  it('with drafts shown, onlyDrafts leaves the drafts', () => {
    expect(titles(board({ drafts: true }), true)).toEqual(['draft', 'draft busy', 'draft reviewed'])
  })

  it('with drafts hidden, onlyDrafts leaves only the drafts the hide rule spares', () => {
    // a hidden draft is still shown while it is busy or carries a review; onlyDrafts must not
    // resurrect the ones the drafts setting already removed
    expect(titles(board({ drafts: false }), true)).toEqual(['draft busy', 'draft reviewed'])
  })

  it('with drafts hidden and onlyDrafts off, no plain draft survives', () => {
    expect(titles(board({ drafts: false }), false)).toEqual(['plain', 'draft busy', 'draft reviewed'])
  })
})

describe('selected', () => {
  it('falls back to the first row when the selection is gone', () => {
    const rows = flat(secs(state([{ name: 'MINE', prs: [pr({ title: 'a' }), pr({ title: 'b' })] }])), [ALL], {})
    expect(selected(rows, rows[1].uid)?.title).toBe('b')
    expect(selected(rows, 'a uid from a board that moved')?.title).toBe('a')
    expect(selected([], 'anything')).toBeNull()
  })
})
