import { describe, expect, it } from 'vitest'
import { follow, fuzzy, step, moved, isLogin, people, unfollow, followAll, shownShift, lineOnScreen } from './stories'

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

describe('following a whole team or org', () => {
  const people = (n: number) => Array.from({ length: n }, (_, i) => `dev${i}`)

  it('adds only who fits under the cap, and says how many did not', () => {
    const had = people(48).map((login) => ({ login }))
    const { next, added, left } = followAll(had, ['dev1', 'amy', 'bob', 'cat'], 50)
    expect(next).toHaveLength(50)
    expect([added, left]).toEqual([2, 1])
  })

  it('does not count someone already followed as added or as left out', () => {
    expect(followAll([{ login: 'Amy' }], ['amy', 'bob'], 50)).toEqual({ next: [{ login: 'Amy' }, { login: 'bob' }], added: 1, left: 0 })
  })
})

describe('a dismissed change of direction', () => {
  it('is not raised again by an answer to a poll that started before it was dismissed', () => {
    expect(shownShift('an auth rewrite', 100, 150)).toBe('')
    expect(shownShift('an auth rewrite', 200, 150)).toBe('an auth rewrite')
    expect(shownShift(undefined, 200, 0)).toBe('')
  })
})

describe('the direction line in an open pop-up', () => {
  it('stays while the pop-up is open, even after a poll brings no shift', () => {
    expect(lineOnScreen(true, 'an auth rewrite', '')).toBe('an auth rewrite')
  })
  it('follows the poll once it is closed, or when it opened with none', () => {
    expect(lineOnScreen(false, 'an auth rewrite', '')).toBe('')
    expect(lineOnScreen(true, '', 'a migration')).toBe('a migration')
  })
})

describe('step', () => {
  it('wraps both ways and starts from nothing highlighted', () => {
    expect(step(-1, 3, true)).toBe(0)
    expect(step(-1, 3, false)).toBe(2)
    expect(step(2, 3, true)).toBe(0)
    expect(step(0, 3, false)).toBe(2)
    expect(step(1, 3, false)).toBe(0)
  })
})
