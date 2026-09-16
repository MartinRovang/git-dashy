// Who the footer story pills follow. The server keeps the list (~/.prs_stories.json): this page's own
// storage is per origin, and the port changes every launch.

export type Followed = { login: string }

/** GitHub logins are case-insensitive, so every lookup here is too. */
const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase()

/** Add once. */
export const follow = (list: Followed[], login: string): Followed[] =>
  list.some((f) => same(f.login, login)) ? list : [...list, { login }]

export const unfollow = (list: Followed[], login: string) => list.filter((f) => !same(f.login, login))


/** The server's login_ok: letters, digits and single hyphens, not at either end, at most 39. */
export const isLogin = (s: string) => s.length <= 39 && /^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$/.test(s)

/** Everyone on the board: PR authors and reviewers. `reviewers` is "✓bob ·carol", a glyph before each login;
 *  bots ("app/x", "x[bot]") are not logins and drop out. */
export function people(prs: { author: string; reviewers: string }[]): string[] {
  const seen = new Map<string, string>()
  for (const p of prs)
    for (const raw of [p.author, ...p.reviewers.split(' ')]) {
      const login = raw.replace(/^[^A-Za-z0-9]+/, '')
      if (isLogin(login) && !seen.has(login.toLowerCase())) seen.set(login.toLowerCase(), login)
    }
  return [...seen.values()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
}


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

export type Pr = { repo: string; number: number; title: string; url: string; head?: string }
export type Got = {
  at: number
  summary: string
  prs: Pr[]
  /** One sentence, only when the model judged this a different problem from the last story. */
  shift?: string
}

/** PRs in `next` that were pushed to (head commit changed) or never seen before, then `seen` learns them.
 *  ponytail: `seen` only grows. Search drops PRs from a page at random and hands them back a poll later;
 *  against only the last poll those came back as new work. A PR with no known head (a story cached before
 *  heads) is learnt, not reported. */
export function moved(seen: Map<string, string | undefined>, next: Got): Pr[] {
  const out = next.prs.filter((p) => (seen.has(p.url) ? !!seen.get(p.url) && seen.get(p.url) !== p.head : true))
  for (const p of next.prs) seen.set(p.url, p.head)
  return out
}

/** How many people the server follows at most: story::clean takes 50. */
export const FOLLOW_MAX = 50

/** Follow everyone in `who`, as far as the cap allows: the new list, how many were added, how many did not fit.
 *
 *  ponytail: counted here, not after the fact. The server cuts the list at FOLLOW_MAX, so "following 80 from
 *  org:acme" on a big org claimed people it never kept. */
export function followAll(list: Followed[], who: string[], max = FOLLOW_MAX): { next: Followed[]; added: number; left: number } {
  let next = list
  let added = 0
  let left = 0
  for (const login of who) {
    const grown = follow(next, login)
    if (grown === next) continue // already followed
    if (next.length >= max) left++
    else {
      next = grown
      added++
    }
  }
  return { next, added, left }
}

/** The shift an answer may show, given when its request began and when a shift was last dismissed.
 *
 *  ponytail: a poll that STARTED before the dismissal answers with the shift still on it -- the server had not
 *  been told yet -- and raising it again would undo the dismissal the moment it was made. */
export const shownShift = (shift: string | undefined, began: number, dismissedAt: number) => (began < dismissedAt ? '' : shift || '')
