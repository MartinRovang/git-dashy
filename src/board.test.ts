import { describe, expect, it } from 'vitest'
import type { Pr, Section, StateData } from './types'
import { ALL, buckets, chips, counts, emptyLine, flat, forView, inBucket, inScope, isRead, onScreen, pick, pickBucket, remember, selected, visible, walkBucket, UNFOLDED } from './board'

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
    checks: '✓',
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
    options: { model: [], depth: [], effort: [], voice: [], hunter: [], subs: [], window: [], interval: [], theme: [], scopes: [] },
    knowledge: { memory: '', store: '', teams: [], teamError: '', notes: [] },
    asks: [],
    notices: [],
  }
}

const secs = (d: StateData, failing = false, drafts = false) => visible(d, '', failing, drafts)

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

  it('an unknown name alongside a real one contributes nothing', () => {
    expect(inBucket(v(), ['RETIRED', 'MINE']).map((s) => s.name)).toEqual(['MINE'])
  })

  // nothing produces this today, but the tab strip reads an empty pick the same way, so the two
  // cannot drift into disagreeing about what no tabs means
  it('an empty pick is no sections, the way no tabs selected looks', () => {
    expect(inBucket(v(), [])).toEqual([])
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

  it('counts only what is on screen: a folded section is not marked read', () => {
    const v = secs(state([
      { name: 'MINE', prs: [pr({ title: 'a' })] },
      { name: 'REVIEWED', prs: [pr({ title: 'r' })] },
    ]))
    expect(onScreen(v, [ALL]).map((r) => r.title)).toEqual(['a'])
    expect(onScreen(v, [ALL], { REVIEWED: true }).map((r) => r.title)).toEqual(['a', 'r'])
  })

  it('folds REVIEWED and OTHER while they share the board, never under its own tab', () => {
    const v = secs(state([
      { name: 'MINE', prs: [pr({ title: 'a' })] },
      { name: 'REVIEWED', prs: [pr({ title: 'r' })] },
      { name: 'OTHER', prs: [pr({ title: 'o' })] },
    ]))
    expect(flat(v, [ALL], {}).map((r) => r.title)).toEqual(['a'])
    expect(flat(v, [ALL], {}, { REVIEWED: true }).map((r) => r.title)).toEqual(['a', 'r'])
    expect(flat(v, ['OTHER'], {}).map((r) => r.title)).toEqual(['o'])
    expect(flat(v, ['REVIEWED'], {}).map((r) => r.title)).toEqual(['r'])
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

describe('emptyLine', () => {
  const d = state([], { window: 24 })

  it('names the queue when nothing is narrowing the board', () => {
    expect(emptyLine(d, 'MINE', '', false, false)).toBe('Nothing of yours is open.')
    expect(emptyLine(d, 'REVIEW REQUESTED', '', false, false)).toBe('Nobody is waiting on your review.')
  })

  // the claim is about the QUEUE, so a filter emptying the section makes it false
  it('blames the filter, not the queue, whenever one is on', () => {
    expect(emptyLine(d, 'MINE', 'foo', false, false)).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'MINE', '', true, false)).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'MINE', '', false, true)).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'REVIEWED', 'foo', false, false)).toBe('Nothing matches the filter.')
  })

  it('a query of nothing but spaces is not a filter', () => {
    expect(emptyLine(d, 'MINE', '   ', false, false)).toBe('Nothing of yours is open.')
  })

  it('REVIEWED names the window it is cut to, and says so only when one is set', () => {
    expect(emptyLine(d, 'REVIEWED', '', false, false)).toBe('Nothing reviewed in the last 24h.')
    expect(emptyLine(state([], { window: null }), 'REVIEWED', '', false, false)).toBe('Nothing reviewed yet.')
  })

  it('a section it has no line for still says something', () => {
    expect(emptyLine(d, 'SOMETHING NEW', '', false, false)).toBe('Nothing here.')
  })
})

describe('pick: a chosen row and a fallback are not the same thing', () => {
  const rows = () => flat(secs(state([{ name: 'MINE', prs: [pr({ title: 'a' }), pr({ title: 'b' })] }])), [ALL], {})

  it('says so when the selection is really on the board', () => {
    const r = rows()
    expect(pick(r, r[1].uid)).toEqual({ row: r[1], chosen: true })
  })

  // switching tab lands on rows[0] of the new queue; marking that read claims you looked at it
  it('falls back to the first row and says it was a guess', () => {
    const r = rows()
    expect(pick(r, 'a uid from a board that moved')).toEqual({ row: r[0], chosen: false })
  })

  it('an empty board is neither', () => {
    expect(pick([], 'anything')).toEqual({ row: null, chosen: false })
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

describe('forView: the graph draws the whole board', () => {
  const dirty = { query: 'api', failing: true, drafts: true, bucket: ['MINE'] }

  it('switching to the graph clears every part of the filter row', () => {
    // the bucket is the one that bit: a node outside the pick resolved to a uid flat() never made,
    // and selected() answered with rows[0] — the pane opened on some other PR
    expect(forView('graph', dirty)).toEqual({ query: '', failing: false, drafts: false, bucket: [ALL] })
  })

  it('switching back to the board changes nothing, so your tab survives the round trip', () => {
    expect(forView('board', dirty)).toBe(dirty)
  })
})

describe('walkBucket', () => {
  const keys = [ALL, 'MINE', 'ASSIGNED']

  it('steps one tab and replaces the pick', () => {
    expect(walkBucket(keys, [ALL], 1)).toEqual(['MINE'])
    expect(walkBucket(keys, ['MINE'], 1)).toEqual(['ASSIGNED'])
    expect(walkBucket(keys, ['MINE'], -1)).toEqual([ALL])
  })

  it('wraps at both ends', () => {
    expect(walkBucket(keys, ['ASSIGNED'], 1)).toEqual([ALL])
    expect(walkBucket(keys, [ALL], -1)).toEqual(['ASSIGNED'])
  })

  // a stacked pick is on no single tab, so the walk starts from All rather than guessing
  it('collapses a stacked pick to one tab', () => {
    expect(walkBucket(keys, ['MINE', 'ASSIGNED'], 1)).toEqual(['MINE'])
    expect(walkBucket(keys, ['MINE', 'ASSIGNED'], -1)).toEqual(['ASSIGNED'])
  })

  it('leaves a pick the server no longer has a tab for alone rather than throwing', () => {
    expect(walkBucket([], ['MINE'], 1)).toEqual(['MINE'])
    expect(walkBucket(keys, ['RETIRED'], 1)).toEqual(['MINE'])
  })
})

describe('chips', () => {
  const board = () =>
    state([
      { name: 'MINE', prs: [pr({ checks: '✗' }), pr({ isDraft: true }), pr({ isDraft: true, checks: '✗' })] },
      { name: 'ASSIGNED', prs: [pr({ checks: '✗' }), pr({ checks: '✗' })] },
    ])
  const by = (bucket: string[], failing = false, drafts = false) =>
    Object.fromEntries(chips(secs(board(), failing, drafts), bucket, failing, drafts).map((c) => [c.key, c]))

  it('counts over the bucket on screen, not the whole board', () => {
    expect(by([ALL]).failing.n).toBe(4)
    expect(by(['MINE']).failing.n).toBe(2)
    expect(by(['ASSIGNED']).failing.n).toBe(2)
  })

  it('counts drafts the same way', () => {
    expect(by([ALL]).drafts.n).toBe(2)
    expect(by(['ASSIGNED']).drafts.n).toBe(0)
  })

  it('goes dead when pressing it would bring nothing back', () => {
    expect(by(['ASSIGNED']).drafts.off).toBe(true)
  })

  it('stays live while it is itself on, so you can always turn it off', () => {
    expect(by(['ASSIGNED'], false, true).drafts.off).toBe(false)
  })

  // the pair has to be combinable in either order: a zero here may only be zero because the OTHER
  // filter is hiding the rows this one would have counted
  // A review read "CI failing on makes Drafts read 0" as a wrong number. It is not: the count is how
  // many rows pressing the chip would LEAVE, and filtering to drafts then counting drafts is the
  // same set as counting drafts first. These four pin that, so the claim is not re-raised.
  it('reads the same whether its own filter is on or off', () => {
    expect(by([ALL], false, false).drafts.n).toBe(2)
    expect(by([ALL], false, true).drafts.n).toBe(2)
    expect(by([ALL], false, false).failing.n).toBe(4)
    expect(by([ALL], true, false).failing.n).toBe(4)
  })

  it('shows what the pair would leave, not what one of them would', () => {
    // MINE holds one failing non-draft, one plain draft, and one draft that is also failing
    expect(by(['MINE'], true, false).drafts.n).toBe(1)
    expect(by(['MINE'], false, true).failing.n).toBe(1)
  })

  it('reports on for the filter that is on', () => {
    expect(by([ALL], true, false).failing.on).toBe(true)
    expect(by([ALL], true, false).drafts.on).toBe(false)
  })
})

describe('counts: what the status block reads', () => {
  it('puts each review state under its own label', () => {
    const d = state([
      {
        name: 'REVIEWED',
        prs: [
          pr({ review: '✓ approved' }),
          pr({ review: '✓ approved' }),
          pr({ review: '✗ changes requested' }),
          pr({ review: '~ commented' }),
          pr({ review: '' }),
        ],
      },
    ])
    const got = Object.fromEntries(counts(d).map(([l, n]) => [l, n]))
    expect(got.approved).toBe(2)
    expect(got.changes).toBe(1)
    expect(got.commented).toBe(1)
  })

  it('does not count a TEAM or MERGED verdict twice beside its REVIEWED row', () => {
    const d = state([
      { name: 'MERGED', prs: [pr({ review: '✓ approved' })] },
      { name: 'TEAM', prs: [pr({ review: '✓ approved' })] },
      { name: 'REVIEWED', prs: [pr({ review: '✓ approved' })] },
    ])
    expect(Object.fromEntries(counts(d).map(([l, n]) => [l, n])).approved).toBe(1)
  })

  it('is all zero on an empty board rather than throwing', () => {
    expect(counts(null).map(([, n]) => n)).toEqual([0, 0, 0, 0])
  })
})

describe('visible: the filter box', () => {
  const d = () =>
    state([{ name: 'MINE', prs: [pr({ title: 'export job', repo: 'acme/web', author: 'bob', number: 42 })] }])
  const hits = (q: string) => visible(d(), q, false, false)[0].prs.length

  it('matches the title, the repo, the author and the number', () => {
    expect([hits('export'), hits('acme'), hits('bob'), hits('#42')]).toEqual([1, 1, 1, 1])
  })

  it('ignores case', () => {
    expect(hits('EXPORT')).toBe(1)
  })

  // a query of nothing but spaces is not a query; untrimmed it matched no row and emptied the board
  it('is not narrowed by whitespace alone', () => {
    expect(hits('   ')).toBe(1)
    expect(hits('')).toBe(1)
  })

  it('leaves nothing when it matches nothing', () => {
    expect(hits('nowhere near this')).toBe(0)
  })
})

describe('visible: the CI filter', () => {
  const d = () =>
    state([{ name: 'MINE', prs: [pr({ title: 'red', checks: '✗' }), pr({ title: 'green', checks: '✓' }), pr({ title: 'none', checks: '' })] }])

  it('off, every row survives', () => {
    expect(visible(d(), '', false, false)[0].prs.map((p) => p.title)).toEqual(['red', 'green', 'none'])
  })

  it('on, only the failing one does — a repo with no checks is not failing', () => {
    expect(visible(d(), '', true, false)[0].prs.map((p) => p.title)).toEqual(['red'])
  })
})

describe('TEAM sources', () => {
  it('matches an org chip by owner, a team chip by binding, and hides an unbound repo', () => {
    expect(inScope({ repo: 'Acme/api', team: '' }, ['org:acme'])).toBe(true)
    expect(inScope({ repo: 'x/y', team: 'core' }, ['team:core'])).toBe(true)
    // unbound under a team-bound owner: pick() gave it no team, so the team chip does not claim it
    expect(inScope({ repo: 'acme/forgotten', team: '' }, ['team:core'])).toBe(false)
  })

  it('splits TEAM into logged verdicts and OTHER, and drops an empty OTHER', () => {
    const on = { scopes: ['org:acme'] }
    const v = secs(state([{ name: 'TEAM', prs: [pr({ repo: 'acme/a', title: 'seen', status: '✓ approved' }), pr({ repo: 'acme/b', title: 'new' }), pr({ repo: 'other/c', title: 'off' })] }], on))
    expect(v.map((s) => [s.name, s.prs.map((p) => p.title)])).toEqual([['TEAM', ['seen']], ['OTHER', ['new']]])
    expect(v[1].prs[0].section).toBe('OTHER')
    const known = secs(state([{ name: 'TEAM', prs: [pr({ repo: 'acme/a', status: '✓ approved' })] }], on))
    expect(known.map((s) => s.name)).toEqual(['TEAM'])
    // MERGED follows the same sources, lands at the bottom below OTHER, and starts folded
    const m = secs(state([
      { name: 'TEAM', prs: [pr({ repo: 'acme/b', title: 'new' })] },
      { name: 'MERGED', prs: [pr({ repo: 'acme/m', title: 'shipped' }), pr({ repo: 'other/x', title: 'off' })] },
      { name: 'REVIEWED', prs: [pr({ title: 'r' })] },
    ], on))
    expect(m.map((s) => [s.name, s.prs.map((p) => p.title)])).toEqual([['TEAM', []], ['REVIEWED', ['r']], ['OTHER', ['new']], ['MERGED', ['shipped']]])
    expect(flat(m, [ALL], {}).map((p) => p.title)).toEqual([])
    expect(flat(m, ['MERGED'], {}).map((p) => p.title)).toEqual(['shipped'])
    // the graph resolves clicks with nothing folded
    expect(flat(m, [ALL], {}, UNFOLDED).map((p) => p.title)).toEqual(['r', 'new', 'shipped'])
    // every source off: no empty TEAM left behind
    expect(secs(state([{ name: 'TEAM', prs: [pr({ repo: 'acme/a' })] }], { scopes: [] })).map((s) => s.name)).toEqual([])
  })
})

describe('remember', () => {
  it('never rewinds a mark, so one PR in two sections does not flip between read and unread', () => {
    const merged = { url: 'u', updatedAt: '2026-09-14T13:00:00Z' } // GitHub's time on the MERGED row
    const reviewed = { url: 'u', updatedAt: '2026-09-14T12:00:00Z' } // the log's time on its REVIEWED row
    const r = remember({}, [merged])
    expect(isRead(r, merged) && isRead(r, reviewed)).toBe(true)
    expect(remember(r, [reviewed])).toEqual(r)
    // a PR that moves past its mark is unread again
    expect(isRead(r, { url: 'u', updatedAt: '2026-09-14T14:00:00Z' })).toBe(false)
    expect(isRead({}, merged)).toBe(false)
  })

  it('marks at the current updatedAt, keeps marks off the board, drops the oldest past the cap', () => {
    const read = { a: '2026-01-01', b: '2026-03-01', c: '2026-02-01' }
    expect(remember(read, [{ url: 'a', updatedAt: '2026-04-01' }])).toEqual({ a: '2026-04-01', b: '2026-03-01', c: '2026-02-01' })
    expect(remember(read, [{ url: 'd', updatedAt: '2026-05-01' }], 2)).toEqual({ d: '2026-05-01', b: '2026-03-01' })
  })
})
