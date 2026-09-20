import { describe, expect, it } from 'vitest'
import { arrange, moved, outside, paneSections, same } from './layout'

describe('arrange: the saved order meets the sections that exist', () => {
  it('drops a saved name that is gone and puts a new one last', () => {
    expect(arrange(['c', 'gone', 'a'], ['a', 'b', 'c'])).toEqual(['c', 'a', 'b'])
  })
  it('is the default order with nothing saved', () => {
    expect(arrange(undefined, ['a', 'b'])).toEqual(['a', 'b'])
  })
})

describe('moved: a drop above or below a section', () => {
  it('puts it above', () => expect(moved(['a', 'b', 'c'], 'c', 'a', false)).toEqual(['c', 'a', 'b']))
  it('puts it below', () => expect(moved(['a', 'b', 'c'], 'a', 'b', true)).toEqual(['b', 'a', 'c']))
  it('leaves a drop on itself alone', () => expect(moved(['a', 'b'], 'a', 'a', true)).toEqual(['a', 'b']))
})

describe('outside: whether a drag ended past the panel', () => {
  const r = { left: 100, right: 300, top: 50, bottom: 500 }
  it('is false inside', () => expect(outside(r, 200, 200)).toBe(false))
  it('is true past an edge', () => expect(outside(r, 800, 200)).toBe(true))
  // a webview that reports no point must not pop every drag out
  it('is false at (0,0)', () => expect(outside(r, 0, 0)).toBe(false))
})

describe('same: the server answering with what we sent', () => {
  it('ignores key order and a missing list', () => expect(same({ off: [], order: ['a'] }, { order: ['a'] })).toBe(true))
  it('sees a changed list', () => expect(same({ order: ['a', 'b'] }, { order: ['b', 'a'] })).toBe(false))
})

describe('paneSections', () => {
  const order = ['about', 'checks', 'review', 'database']
  const body = { about: {} }
  const row = { detail: null, gone: false, review: false, db: false }
  it('drops a section with no body once the detail is in', () => {
    expect(paneSections(order, body, undefined, { ...row, detail: { pending: false } })).toEqual(['about'])
  })
  it('waits on CHECKS alone: the rest are final in the first answer', () => {
    expect(paneSections(order, body, undefined, { ...row, detail: { pending: true } })).toEqual(['about', 'checks'])
  })
  it('holds a place only for what the row says is coming', () => {
    expect(paneSections(order, body, undefined, { ...row, review: true })).toEqual(['about', 'checks', 'review'])
  })
  it('draws no bar for a review the row does not have', () => {
    expect(paneSections(order, body, undefined, row)).toEqual(['about', 'checks'])
  })
  it('promises nothing once the request has given up', () => {
    expect(paneSections(order, body, undefined, { ...row, review: true, db: true, gone: true })).toEqual(['about'])
  })
  it('never shows one switched off, waiting or not', () => {
    expect(paneSections(order, body, ['about', 'checks'], { ...row, review: true })).toEqual(['review'])
  })
})
