import { describe, expect, it } from 'vitest'
import type { PostingRule, Pr, Row, Section, StateData, Talk } from './types'
import { rowState } from './tokens'
import { ALL, NOBODY, UNFOLDED, buckets, canCastOn, castResult, castStep, chips, counts, emptyLine, flat, forView, inBucket, inScope, isRead, isRefetching, isReviewed, onScreen, pick, pickBucket, pickable, hasOwnRule, remember, postingTree, ruleSource, selected, talkControls, toggleHidden, underScope, visible, walkBucket, whoIs, pageTo } from './board'

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
    ticks: 0,
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
    changelog: '',
  }
}

const secs = (d: StateData, failing = false, drafts = false) => visible(d, '', failing, drafts, NOBODY)

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
    const v = visible(state([{ name: 'MINE', prs: [pr({ title: 'keep' })] }]), 'nothing matches', false, false, NOBODY)
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
  const titles = (d: StateData, onlyDrafts: boolean) => visible(d, '', false, onlyDrafts, NOBODY)[0].prs.map((p) => p.title)

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
    expect(emptyLine(d, 'MINE', '', false, false, NOBODY)).toBe('Nothing of yours is open.')
    expect(emptyLine(d, 'REVIEW REQUESTED', '', false, false, NOBODY)).toBe('Nobody is waiting on your review.')
  })

  // the claim is about the QUEUE, so a filter emptying the section makes it false
  it('blames the filter, not the queue, whenever one is on', () => {
    expect(emptyLine(d, 'MINE', 'foo', false, false, NOBODY)).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'MINE', '', true, false, NOBODY)).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'MINE', '', false, true, NOBODY)).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'REVIEWED', 'foo', false, false, NOBODY)).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'MINE', '', false, false, { repos: ['acme/web'], authors: [] })).toBe('Nothing matches the filter.')
    expect(emptyLine(d, 'MINE', '', false, false, { repos: [], authors: ['ann'] })).toBe('Nothing matches the filter.')
  })

  it('a query of nothing but spaces is not a filter', () => {
    expect(emptyLine(d, 'MINE', '   ', false, false, NOBODY)).toBe('Nothing of yours is open.')
  })

  it('REVIEWED names the window it is cut to, and says so only when one is set', () => {
    expect(emptyLine(d, 'REVIEWED', '', false, false, NOBODY)).toBe('Nothing reviewed in the last 24h.')
    expect(emptyLine(state([], { window: null }), 'REVIEWED', '', false, false, NOBODY)).toBe('Nothing reviewed yet.')
  })

  it('a section it has no line for still says something', () => {
    expect(emptyLine(d, 'SOMETHING NEW', '', false, false, NOBODY)).toBe('Nothing here.')
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
  const dirty = { query: 'api', failing: true, drafts: true, hidden: true, bucket: ['MINE'] }

  it('switching to the graph clears every part of the filter row', () => {
    // the bucket is the one that bit: a node outside the pick resolved to a uid flat() never made,
    // and selected() answered with rows[0] — the pane opened on some other PR
    expect(forView('graph', dirty)).toEqual({ query: '', failing: false, drafts: false, hidden: false, bucket: [ALL] })
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
  const hits = (q: string) => visible(d(), q, false, false, NOBODY)[0].prs.length

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

describe('visible: hidden PRs', () => {
  const stale = pr({ title: 'stale', updatedAt: '2026-09-01T00:00:00Z' })
  const d = (over: Partial<Pr> = {}) => state([{ name: 'MINE', prs: [{ ...stale, ...over }, pr({ title: 'live' })] }])
  const titles = (x: StateData, show = false) => visible(x, '', false, false, NOBODY, { [stale.url]: stale.updatedAt }, show)[0].prs.map((p) => p.title)

  it('leaves a hidden PR out, and the chip shows only the hidden ones', () => {
    expect(titles(d())).toEqual(['live'])
    expect(titles(d(), true)).toEqual(['stale'])
  })

  it('brings it back once it moves past the mark', () => {
    expect(titles(d({ updatedAt: '2026-09-02T00:00:00Z' }))).toEqual(['stale', 'live'])
  })

  it('toggleHidden hides a url at its newest time across sections, and unhides it from any of its rows', () => {
    const rows = [
      { url: 'u', updatedAt: '2026-09-01T00:00:00Z' }, // REVIEWED: the log's time
      { url: 'u', updatedAt: '2026-09-03T00:00:00Z' }, // MERGED: GitHub's
      { url: 'other', updatedAt: '2026-09-05T00:00:00Z' },
    ]
    const on = toggleHidden({ keep: 't' }, rows, 'u')
    expect(on).toEqual({ keep: 't', u: '2026-09-03T00:00:00Z' })
    expect(rows.slice(0, 2).every((r) => isRead(on, r))).toBe(true)
    expect(toggleHidden(on, rows, 'u')).toEqual({ keep: 't' })
  })
})

describe('visible: the CI filter', () => {
  const d = () =>
    state([{ name: 'MINE', prs: [pr({ title: 'red', checks: '✗' }), pr({ title: 'green', checks: '✓' }), pr({ title: 'none', checks: '' })] }])

  it('off, every row survives', () => {
    expect(visible(d(), '', false, false, NOBODY)[0].prs.map((p) => p.title)).toEqual(['red', 'green', 'none'])
  })

  it('on, only the failing one does — a repo with no checks is not failing', () => {
    expect(visible(d(), '', true, false, NOBODY)[0].prs.map((p) => p.title)).toEqual(['red'])
  })
})

describe('TEAM sources', () => {
  it('matches an org chip by owner, a team chip by binding, and hides an unbound repo', () => {
    expect(inScope({ repo: 'Acme/api', team: '' }, ['org:acme'])).toBe(true)
    expect(inScope({ repo: 'x/y', team: 'core' }, ['team:core'])).toBe(true)
    // unbound under a team-bound owner: pick() gave it no team, so the team chip does not claim it
    expect(inScope({ repo: 'acme/forgotten', team: '' }, ['team:core'])).toBe(false)
  })

  it('does not follow you, when your own PRs are under the chip', () => {
    const d = state([
      { name: 'MINE', prs: [pr({ repo: 'acme/api', author: 'Me' })] },
      { name: 'TEAM', prs: [pr({ repo: 'acme/web', author: 'amy', reviewers: '✓me' })] },
    ])
    expect(underScope(d, 'org:acme')).toEqual(['amy'])
  })

  it('collects everyone under one chip, once each, and nobody from a chip with no rows', () => {
    const d = state([
      { name: 'REVIEW REQUESTED', prs: [pr({ repo: 'Acme/api', author: 'bob', reviewers: '✓carol ·bob' })] },
      { name: 'TEAM', prs: [pr({ repo: 'acme/web', author: 'amy' }), pr({ repo: 'other/x', author: 'dave' })] },
    ])
    // bob authors one and reviews it: once. dave is under another owner: not at all.
    expect(underScope(d, 'org:acme')).toEqual(['amy', 'bob', 'carol'])
    expect(underScope(d, 'org:other')).toEqual(['dave'])
    expect(underScope(d, 'team:core')).toEqual([])
    expect(underScope(null, 'org:acme')).toEqual([])
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
    // the log writes +00:00 and GitHub Z: the same second is read either way, a later one is not
    const log = { u: '2026-09-14T13:00:00+00:00' }
    expect(isRead(log, merged)).toBe(true)
    expect(isRead(log, { url: 'u', updatedAt: '2026-09-14T13:00:01Z' })).toBe(false)
    expect(isRead(remember(log, [merged]), { url: 'u', updatedAt: '2026-09-14T12:59:59+00:00' })).toBe(true)
  })

  it('marks at the current updatedAt, keeps marks off the board, drops the oldest past the cap', () => {
    const read = { a: '2026-01-01', b: '2026-03-01', c: '2026-02-01' }
    expect(remember(read, [{ url: 'a', updatedAt: '2026-04-01' }])).toEqual({ a: '2026-04-01', b: '2026-03-01', c: '2026-02-01' })
    expect(remember(read, [{ url: 'd', updatedAt: '2026-05-01' }], 2)).toEqual({ d: '2026-05-01', b: '2026-03-01' })
  })
})

describe('isRefetching', () => {
  it('shows only while a fetch runs and none has landed since the history change', () => {
    expect(isRefetching(100, { fetching: true, fetchedAt: 100 })).toBe(true)
    expect(isRefetching(100, { fetching: true, fetchedAt: 200 })).toBe(false)
    expect(isRefetching(100, { fetching: false, fetchedAt: 100 })).toBe(false)
    expect(isRefetching(null, { fetching: true, fetchedAt: 100 })).toBe(false)
  })
})

describe('rowState: a held review is not a posted one', () => {
  it('says it is waiting, ahead of the verdict it carries', () => {
    // the hold path puts the verdict in `review`, so without the guard the row read as posted
    const r = rowState(pr({ waiting: true, review: '✗ changes requested (waiting to post)' }))
    expect(r).toEqual({ key: 'waiting', label: 'waiting to post' })
  })

  it('a posted verdict still reads as the verdict', () => {
    expect(rowState(pr({ review: '✗ changes requested' }))).toEqual({
      key: 'changes',
      label: 'changes requested',
    })
  })

  // a review in flight outranks it: the spinner is what is happening right now
  it('busy still wins', () => {
    expect(rowState(pr({ waiting: true, busy: true })).key).toBe('running')
  })
})

describe('isReviewed: a held verdict is not a posted one', () => {
  it('a posted verdict counts', () => {
    expect(isReviewed(pr({ review: '✗ changes requested' }))).toBe(true)
    expect(isReviewed(pr({ review: '✓ approved' }))).toBe(true)
  })

  // the hold path writes the verdict into `review` so the row can say "waiting to post", and tone()
  // matches it — every caller that forgot this treated a held review as one the author had seen
  it('a held verdict does not, however it reads', () => {
    expect(isReviewed(pr({ waiting: true, review: '✗ changes requested (waiting to post)' }))).toBe(false)
    expect(isReviewed(pr({ waiting: true, review: '✓ approved (waiting to post)' }))).toBe(false)
  })

  it('no verdict at all is not reviewed', () => {
    expect(isReviewed(pr())).toBe(false)
    expect(isReviewed(pr({ review: 'reviewing…' }))).toBe(false)
  })
})

describe('repo and author picks', () => {
  const d = state([
    { name: 'MINE', prs: [pr({ title: 'a', repo: 'acme/web', author: 'bob' }), pr({ title: 'b', repo: 'acme/api', author: 'ann' })] },
    { name: 'ASSIGNED', prs: [pr({ title: 'c', repo: 'acme/web', author: 'ann' })] },
  ])
  const titles = (repos: string[], authors: string[]) => visible(d, '', false, false, { repos, authors }).flatMap((s) => s.prs.map((p) => p.title))

  it('an empty pick is everything', () => expect(titles([], [])).toEqual(['a', 'b', 'c']))
  it('narrows by repo', () => expect(titles(['acme/web'], [])).toEqual(['a', 'c']))
  it('narrows by author', () => expect(titles([], ['ann'])).toEqual(['b', 'c']))
  it('repo and author both apply', () => expect(titles(['acme/web'], ['ann'])).toEqual(['c']))
  it('lists the options off the raw board, sorted and once each', () =>
    expect(whoIs(d)).toEqual({ repos: ['acme/api', 'acme/web'], authors: ['ann', 'bob'] }))
  it('the pick and the filter box both apply', () =>
    expect(visible(d, 'ann', false, false, { repos: ['acme/web'], authors: [] }).flatMap((s) => s.prs.map((p) => p.title))).toEqual(['c']))
  it('an empty author is not an option', () =>
    expect(whoIs(state([{ name: 'MINE', prs: [pr({ repo: 'acme/web', author: '' })] }])).authors).toEqual([]))
  it('a pick whose PRs left the board stays listed so it can be unticked', () =>
    expect(pickable(whoIs(d), { repos: ['acme/gone'], authors: [] }, 'repos')).toEqual(['acme/api', 'acme/web', 'acme/gone']))
})

describe('what the review screen lets you press', () => {
  const talk = (over: Partial<Talk> = {}): Talk => ({ instructions: '', thread: [], proposed: null, cannotDiscuss: '', busy: false, ...over })
  const said = { who: 'you' as const, text: 'why?', at: 1 }
  const revision = { verdict: 'comment', summary: '', body: 'b' }

  it('sends only something typed, and revises only after a conversation', () => {
    expect(talkControls(talk(), '')).toEqual({ type: true, send: false, revise: false, decide: false })
    expect(talkControls(talk(), '  ').send).toBe(false)
    expect(talkControls(talk(), 'why?').send).toBe(true)
    expect(talkControls(talk({ thread: [said] }), '').revise).toBe(true)
  })

  it('offers accept and keep only while a revision waits, and no second revision then', () => {
    const waiting = talkControls(talk({ thread: [said], proposed: revision }), '')
    expect([waiting.decide, waiting.revise]).toEqual([true, false])
  })

  it('offers nothing while the agent works', () => {
    expect(talkControls(talk({ thread: [said], proposed: revision, busy: true }), 'x')).toEqual({ type: false, send: false, revise: false, decide: false })
  })

  it('offers no conversation where there is none to have', () => {
    const provider = talkControls(talk({ cannotDiscuss: 'discussion needs the claude CLI', thread: [said] }), 'x')
    expect([provider.type, provider.send, provider.revise]).toEqual([false, false, false])
    expect(talkControls(null, 'x')).toEqual({ type: false, send: false, revise: false, decide: false })
  })
})

describe('posting rows', () => {
  it('tells an own rule from an inherited one from nothing set at all', () => {
    // the same `via` reads differently depending on whose row carries it
    expect(ruleSource('acme/*', 'owner')).toBe('own')
    expect(ruleSource('acme/api', 'owner')).toBe('owner')
    expect(ruleSource('acme/api', 'repo')).toBe('own')
    // nothing set is its own answer, not the same as having one
    expect(ruleSource('acme/api', '')).toBe('none')
    expect(ruleSource('acme/*', '')).toBe('none')
  })
})

describe('the posting tree', () => {
  const rule = (target: string, m: ['post' | 'hold', '' | 'repo' | 'owner'], a: ['post' | 'hold', '' | 'repo' | 'owner']) =>
    ({ target, manual: m[0], manualVia: m[1], auto: a[0], autoVia: a[1] }) as PostingRule
  // the screenshot: acme/* holds what you run, three repos under it, one of them carved out on auto
  const board = () => [
    rule('acme/*', ['hold', 'owner'], ['post', '']),
    rule('acme/api', ['hold', 'owner'], ['post', '']),
    rule('acme/infra', ['hold', 'owner'], ['post', '']),
    rule('acme/web', ['hold', 'owner'], ['post', 'repo']),
  ]

  it('puts each repo under the owner that owns it', () => {
    const [acme] = postingTree(board())
    expect(acme.owner.target).toBe('acme/*')
    expect(acme.repos.map((r) => r.target)).toEqual(['acme/api', 'acme/infra', 'acme/web'])
    expect(acme.governs).toBe(true)
  })

  it('says an owner with no rule of its own does not govern', () => {
    // nothing set anywhere: the repos below are where a first rule gets set, so they keep their controls
    const [acme] = postingTree([rule('acme/*', ['post', ''], ['post', '']), rule('acme/api', ['post', ''], ['post', ''])])
    expect(acme.governs).toBe(false)
    expect(acme.repos.map((r) => r.target)).toEqual(['acme/api'])
  })

  it('keeps owners apart and in the order they arrived', () => {
    const tree = postingTree([...board(), rule('zeta/*', ['hold', 'owner'], ['post', '']), rule('zeta/one', ['hold', 'owner'], ['post', ''])])
    expect(tree.map((n) => n.owner.target)).toEqual(['acme/*', 'zeta/*'])
    expect(tree[1].repos.map((r) => r.target)).toEqual(['zeta/one'])
    expect(tree[0].repos).toHaveLength(3)
  })

  it('gives a repo whose owner row never arrived a parent of its own', () => {
    // no row is ever dropped: an owner nobody sent is built from the repo's name and governs nothing
    const [n] = postingTree([rule('other/x', ['post', ''], ['post', ''])])
    expect([n.owner.target, n.governs, n.repos.map((r) => r.target)]).toEqual(['other/*', false, ['other/x']])
  })

  it('keeps the repo that overrides a governing owner apart, so the panel can show it', () => {
    const [acme] = postingTree(board())
    // acme/web has a rule of its own on auto, which beats acme/*; the other two have nothing
    expect(acme.repos.map(hasOwnRule)).toEqual([false, false, true])
    expect(acme.exceptions.map((r) => r.target)).toEqual(['acme/web'])
  })

  it('does not read a per-repo owner\'s fallback rule as deciding for its repos', () => {
    const owner = { ...rule('acme/*', ['post', ''], ['hold', 'owner']), perRepo: true }
    const [acme] = postingTree([owner, rule('acme/api', ['post', ''], ['post', 'repo'])])
    expect([acme.governs, acme.exceptions]).toEqual([false, []])
    expect(postingTree([{ ...owner, perRepo: false }])[0].governs).toBe(true)
  })

  it('has no exceptions under an owner that does not govern, where every repo is set on its own', () => {
    const [acme] = postingTree([rule('acme/*', ['post', ''], ['post', '']), rule('acme/api', ['hold', 'repo'], ['post', ''])])
    expect(acme.governs).toBe(false)
    expect(acme.exceptions).toEqual([])
  })
})

describe('paging a screen that shows one item at a time', () => {
  it('steps and wraps at both ends', () => {
    expect([pageTo(0, 1, 3), pageTo(2, 1, 3), pageTo(0, -1, 3)]).toEqual([1, 0, 2])
  })
  it('stays put on one item and does not divide by zero on none', () => {
    expect([pageTo(0, 1, 1), pageTo(0, -1, 0)]).toEqual([0, 0])
  })
})

describe('canCastOn', () => {
  const row = (over: Partial<Row> = {}): Row => ({ ...pr(), uid: 'u', section: 'REVIEW REQUESTED', older: [], ...over })
  it('casts on any PR, reviewed or not, whenever nothing else is running on it', () => {
    expect(canCastOn(row())).toBe(true)
    expect(canCastOn(row({ section: 'MINE' }))).toBe(true)
    expect(canCastOn(row({ review: '✓ approved' }))).toBe(true)
    expect(canCastOn(row({ busy: true }))).toBe(false)
    expect(canCastOn(row({ humanOnly: true }))).toBe(false)
  })
})

describe('a cast started here', () => {
  const cast = () => ({ spell: 'tests', since: 1000, busy: false })
  it('ends only once its row was seen running and has stopped', () => {
    const c = cast()
    expect(castStep(c, pr())).toBe('wait') // never seen busy: the poll may predate the cast
    expect(castStep(c, pr({ busy: true }))).toBe('wait')
    expect(castStep(c, pr())).toBe('ended')
  })
  it('is let go when its PR leaves the board', () => {
    expect(castStep(cast(), undefined)).toBe('gone')
  })
  it('takes only a result of the same spell written since the cast', () => {
    const c = cast()
    expect(castResult([{ name: 'tests', at: 900 }], c)).toBeUndefined() // an older run
    expect(castResult([{ name: 'perf', at: 1005 }], c)).toBeUndefined()
    expect(castResult([], c)).toBeUndefined() // the cast failed
    expect(castResult([{ name: 'tests', at: 1005 }], c)).toEqual({ name: 'tests', at: 1005 })
  })
})
