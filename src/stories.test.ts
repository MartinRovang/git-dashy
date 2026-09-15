import { describe, expect, it } from 'vitest'
import { follow, fuzzy, moved, isLogin, people, unfollow } from './stories'

describe('followed users', () => {
  it('adds a login once, and drops it again', () => {
    const one = follow([], 'Bob')
    expect(one).toEqual([{ login: 'Bob' }])
    expect(follow(one, 'bob')).toBe(one)
    expect(unfollow(one, 'BOB')).toEqual([])
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

it('only PRs pushed to or never seen count as new work', () => {
  const pr = (n: number, head: string | undefined = 'a') => ({ repo: 'acme/api', number: n, title: `t${n}`, url: `https://gh/acme/api/pull/${n}`, head })
  const got = (...prs: ReturnType<typeof pr>[]) => ({ at: 1, summary: '', prs })
  const seen = new Map<string, string | undefined>()
  moved(seen, got(pr(1), pr(2), pr(3)))
  expect(moved(seen, got(pr(1), pr(2, 'b'), pr(4))).map((p) => p.number)).toEqual([2, 4])
  // search leaving pr 3 out, then handing it back, is nothing new
  expect(moved(seen, got(pr(1), pr(2, 'b')))).toEqual([])
  expect(moved(seen, got(pr(1), pr(2, 'b'), pr(3)))).toEqual([])
  // a story cached before heads existed
  const old = new Map<string, string | undefined>()
  moved(old, got(pr(1, undefined)))
  expect(moved(old, got(pr(1)))).toEqual([])
})
