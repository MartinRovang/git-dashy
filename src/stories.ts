// Who the floating story cards follow, kept in this browser's storage.

export type Followed = { login: string; days: number }
const KEY = 'stories'

export function loadFollowed(): Followed[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]') as Followed[]
  } catch {
    return []
  }
}

export function saveFollowed(list: Followed[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    /* storage unavailable */
  }
}

/** Add once (GitHub logins are case-insensitive), a week by default. */
export const follow = (list: Followed[], login: string): Followed[] =>
  list.some((f) => f.login.toLowerCase() === login.toLowerCase()) ? list : [...list, { login, days: 7 }]

export const unfollow = (list: Followed[], login: string) => list.filter((f) => f.login !== login)

export const setDays = (list: Followed[], login: string, days: number) =>
  list.map((f) => (f.login === login ? { ...f, days } : f))
