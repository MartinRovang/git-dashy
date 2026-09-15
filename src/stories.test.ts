import { describe, expect, it } from 'vitest'
import { follow, setDays, unfollow } from './stories'

describe('followed users', () => {
  it('adds a login once, a week by default, and drops it again', () => {
    const one = follow([], 'Bob')
    expect(one).toEqual([{ login: 'Bob', days: 7 }])
    expect(follow(one, 'bob')).toBe(one)
    expect(setDays(one, 'Bob', 3)).toEqual([{ login: 'Bob', days: 3 }])
    expect(unfollow(one, 'Bob')).toEqual([])
  })
})
