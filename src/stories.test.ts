import { describe, expect, it } from 'vitest'
import { follow, fuzzy, patch, people, unfollow } from './stories'

describe('followed users', () => {
  it('adds a login once, and drops it again', () => {
    const one = follow([], 'Bob')
    expect(one).toEqual([{ login: 'Bob', open: false, min: false }])
    expect(follow(one, 'bob')).toBe(one)
    expect(patch(one, 'Bob', { min: true })).toEqual([{ login: 'Bob', open: false, min: true }])
    expect(unfollow(one, 'Bob')).toEqual([])
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

  it('matches letters in order, a start and a run ranking first', () => {
    const logins = ['marvin', 'MartinRovang', 'amrov', 'bob']
    expect(fuzzy('mrov', logins)).toEqual(['MartinRovang', 'amrov'])
    expect(fuzzy('mar', logins).slice(0, 2)).toEqual(['marvin', 'MartinRovang'])
    expect(fuzzy('zz', logins)).toEqual([])
    expect(fuzzy('', logins)).toBe(logins)
  })
})
