// Who the floating story cards follow. The server keeps the list (~/.prs_stories.json): this page's own
// storage is per origin, and the port changes every launch.

export type Followed = { login: string; days: number }

/** Add once (GitHub logins are case-insensitive), three days by default. */
export const follow = (list: Followed[], login: string): Followed[] =>
  list.some((f) => f.login.toLowerCase() === login.toLowerCase()) ? list : [...list, { login, days: 3 }]

export const unfollow = (list: Followed[], login: string) => list.filter((f) => f.login !== login)

export const setDays = (list: Followed[], login: string, days: number) =>
  list.map((f) => (f.login === login ? { ...f, days } : f))

const LOGIN = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/

/** Everyone on the board: PR authors and reviewers. `reviewers` is "✓bob ·carol", a glyph before each login;
 *  bots ("app/x", "x[bot]") are not logins and drop out. */
export function people(prs: { author: string; reviewers: string }[]): string[] {
  const seen = new Map<string, string>()
  for (const p of prs)
    for (const raw of [p.author, ...p.reviewers.split(' ')]) {
      const login = raw.replace(/^[^A-Za-z0-9]+/, '')
      if (LOGIN.test(login) && !seen.has(login.toLowerCase())) seen.set(login.toLowerCase(), login)
    }
  return [...seen.values()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
}

export const isLogin = (s: string) => LOGIN.test(s)

/** The logins whose letters hold `q` in order, best first: a match at the start, then letters in a row,
 *  then an earlier start. Empty `q` keeps them all, in order. */
export function fuzzy(q: string, logins: string[]): string[] {
  const needle = q.toLowerCase()
  if (!needle) return logins
  const scored: [number, string][] = []
  for (const login of logins) {
    const hay = login.toLowerCase()
    let at = -1
    let run = 0
    let score = 0
    let first = -1
    for (const ch of needle) {
      const i = hay.indexOf(ch, at + 1)
      if (i < 0) {
        score = -1
        break
      }
      run = i === at + 1 ? run + 1 : 0
      score += run * 2
      if (first < 0) first = i
      at = i
    }
    if (score < 0) continue
    scored.push([score + (first === 0 ? 10 : 0) - first * 0.1, login])
  }
  return scored.sort((a, b) => b[0] - a[0]).map(([, l]) => l)
}
