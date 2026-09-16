import { describe, expect, it } from 'vitest'
import type { Detail, Row } from '../types'
import { acts } from './Acts'

function row(over: Partial<Row> = {}): Row {
  return {
    url: 'https://x/1',
    number: 1,
    title: 't',
    repo: 'acme/web',
    author: 'bob',
    updatedAt: '',
    isDraft: false,
    status: '',
    prev: '',
    checks: '',
    reviewers: '',
    review: '',
    busy: false,
    team: '',
    summary: '',
    reviewAt: '',
    kind: '',
    breaking: false,
    pre: null,
    uid: 'u',
    section: 'REVIEW REQUESTED',
    older: [],
    ...over,
  }
}

const keys = (p: Row, d: Detail | null = null) => acts(p, d).map(([, , , , , act]) => act)
const one = (p: Row, name: string) => acts(p, null).find(([, , , , , act]) => act === name)

describe('acts: what one PR offers', () => {
  it('offers hide, or unhide on a hidden row', () => {
    expect(acts(row(), null).find(([, , , , , act]) => act === 'hide')?.[2]).toBe('Hide this PR')
    expect(acts(row(), null, true).find(([, , , , , act]) => act === 'hide')?.[2]).toBe('Unhide this PR')
  })

  it('every row can be opened, copied, bound and have its memory edited', () => {
    for (const section of ['REVIEW REQUESTED', 'MINE', 'ASSIGNED', 'REVIEWED']) {
      expect(keys(row({ section }))).toEqual(expect.arrayContaining(['code', 'open', 'copy', 'bind', 'memory']))
    }
  })

  it('only a review-requested row offers the review', () => {
    expect(keys(row({ section: 'REVIEW REQUESTED' }))).toContain('review')
    expect(keys(row({ section: 'MINE' }))).not.toContain('review')
    expect(keys(row({ section: 'REVIEWED' }))).not.toContain('review')
  })

  it('offers a review with instructions exactly where it offers a review', () => {
    // the braces matter: a push under an unbraced `if` put it on every row
    for (const section of ['REVIEW REQUESTED', 'MINE', 'ASSIGNED', 'REVIEWED']) {
      expect(keys(row({ section })).includes('ask')).toBe(keys(row({ section })).includes('review'))
    }
    expect(keys(row({ section: 'REVIEW REQUESTED' }))).toContain('ask')
    const busy = row({ section: 'REVIEW REQUESTED', busy: true })
    expect([one(busy, 'review')?.[4], one(busy, 'ask')?.[4]]).toEqual([true, true])
  })

  it('only your own row offers the pre-review and a reviewer request', () => {
    expect(keys(row({ section: 'MINE' }))).toEqual(expect.arrayContaining(['pre', 'reviewer']))
    expect(keys(row({ section: 'ASSIGNED' }))).not.toContain('pre')
    expect(keys(row({ section: 'ASSIGNED' }))).not.toContain('reviewer')
  })

  it('offers the waiting review only on a row that has one', () => {
    expect(keys(row({ waiting: true }))).toContain('waiting')
    expect(keys(row())).not.toContain('waiting')
  })

  it('offers the posting setting on every row', () => {
    expect(keys(row())).toContain('posting')
    expect(keys(row({ section: 'REVIEWED' }))).toContain('posting')
  })

  // the hold path writes "✗ changes requested (waiting to post)" into review, and tone() matches it,
  // so without the guard the menu said Reviewed for a verdict the author has never seen
  it('does not call a held review reviewed', () => {
    const held = row({ section: 'REVIEW REQUESTED', waiting: true, review: '✗ changes requested (waiting to post)' })
    expect(one(held, 'review')?.[2]).toBe('Review this PR')
    expect(one(held, 'review')?.[4]).toBe(false)
  })

  // the two states that must not start a second run on the same head
  it('the review is dead once it is running or already done', () => {
    const rr = (over: Partial<Row>) => one(row({ section: 'REVIEW REQUESTED', ...over }), 'review')
    expect(rr({})?.[4]).toBe(false)
    expect(rr({ busy: true })?.[4]).toBe(true)
    expect(rr({ review: '✓ approved' })?.[4]).toBe(true)
    // a review string with no verdict glyph is not a verdict, so the row is still reviewable
    expect(rr({ review: 'reviewing…' })?.[4]).toBe(false)
  })

  it('says what the pre-review would do, so a stale one is not silently reopened', () => {
    const label = (over: Partial<Row>) => one(row({ section: 'MINE', ...over }), 'pre')?.[2]
    expect(label({})).toBe('Pre-review')
    expect(label({ pre: { at: 1, moved: false } })).toBe('Inspect pre-review')
    expect(label({ pre: { at: 1, moved: true } })).toBe('Re-run the pre-review')
  })

  // the detail belongs to the selected PR, so a right-click elsewhere passes null and loses this row
  it('offers the full review only when its detail is to hand', () => {
    expect(keys(row())).not.toContain('view')
    const d = { url: 'https://x/1', review: { model: 'opus' } } as unknown as Detail
    expect(keys(row(), d)).toContain('view')
  })
})
