import { describe, expect, it } from 'vitest'
import { follow, fuzzy, isLogin, patch, people, unfollow } from './stories'

describe('followed users', () => {
  it('adds a login once, and drops it again', () => {
    const one = follow([], 'Bob')
    expect(one).toEqual([{ login: 'Bob', min: false }])
    expect(follow(one, 'bob')).toBe(one)
    expect(patch(one, 'Bob', { min: true })).toEqual([{ login: 'Bob', min: true }])
    expect(unfollow(one, 'BOB')).toEqual([])
    expect(patch(one, 'bob', { min: true })[0].min).toBe(true)
  })
})

describe('finding someone to follow', () => {
  it('collects authors and reviewers once, without glyphs or bots', () => {
    const prs = [
      { author: 'MartinRovang', reviewers: '✓bob ·carol' },
      { author: 'app/dependabot', reviewers: '~Bob ✗renovate[bot]' },
    ]
    expect(people(prs)).toEqual(['bob', 'carol', 'MartinRovang'])
  })

  it('holds logins to the rule the server checks', () => {
    expect(['bob', 'a-b1', 'A'.repeat(39)].every(isLogin)).toBe(true)
    expect(['', '-x', 'x-', 'a--b', 'a b', 'A'.repeat(40)].some(isLogin)).toBe(false)
  })

  it('matches letters in order, a start and a run ranking first', () => {
    const logins = ['marvin', 'MartinRovang', 'amrov', 'bob']
    expect(fuzzy('mrov', logins)).toEqual(['MartinRovang', 'amrov'])
    expect(fuzzy('mar', logins).slice(0, 2)).toEqual(['marvin', 'MartinRovang'])
    expect(fuzzy('zz', logins)).toEqual([])
    expect(fuzzy('', logins)).toBe(logins)
  })
})
