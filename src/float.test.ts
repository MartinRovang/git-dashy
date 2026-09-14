import { afterAll, describe, expect, it, vi } from 'vitest'
import { fit, type Box } from './float'

const box = (over: Partial<Box> = {}): Box => ({ x: 100, y: 100, w: 400, h: 300, max: false, ...over })

describe('fit: a remembered window comes back on screen', () => {
  // fit() reads two numbers off `window` and nothing else, so it needs two numbers, not a DOM.
  // useFloatBox's hooks, storage and observer are never called here.
  const sized = (innerWidth: number, innerHeight: number) => vi.stubGlobal('window', { innerWidth, innerHeight })
  afterAll(() => vi.unstubAllGlobals())

  it('leaves a box that already fits exactly where it was', () => {
    sized(1200, 800)
    expect(fit(box())).toEqual(box())
  })

  // the case that matters: the window it was saved on was bigger than this one
  it('shrinks a box wider or taller than the window', () => {
    sized(300, 200)
    expect(fit(box())).toEqual({ x: 0, y: 0, w: 300, h: 200, max: false })
  })

  it('pulls a box that starts off the right or bottom edge back in', () => {
    sized(1000, 700)
    expect(fit(box({ x: 900, y: 650 }))).toEqual(box({ x: 600, y: 400 }))
  })

  it('never puts the header off the top or left, where it could not be grabbed', () => {
    sized(1000, 700)
    expect(fit(box({ x: -250, y: -80 }))).toEqual(box({ x: 0, y: 0 }))
  })
})
