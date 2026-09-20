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
  const order = ['about', 'checks', 'review']
  const never = () => false
  it('drops a section with no body once its data is in', () => {
    expect(paneSections(order, { about: {} }, undefined, never)).toEqual(['about'])
  })
  it('holds a place for a section that is still waiting', () => {
    expect(paneSections(order, { about: {} }, undefined, (k) => k === 'checks')).toEqual(['about', 'checks'])
  })
  it('never shows one switched off, waiting or not', () => {
    expect(paneSections(order, { about: {} }, ['about', 'checks'], () => true)).toEqual(['review'])
  })
})
