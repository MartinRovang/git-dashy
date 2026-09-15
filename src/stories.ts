// Who the floating story cards follow. The server keeps the list (~/.prs_stories.json): this page's own
// storage is per origin, and the port changes every launch.

export type Followed = { login: string; days: number }

/** Add once (GitHub logins are case-insensitive), a week by default. */
export const follow = (list: Followed[], login: string): Followed[] =>
  list.some((f) => f.login.toLowerCase() === login.toLowerCase()) ? list : [...list, { login, days: 7 }]

export const unfollow = (list: Followed[], login: string) => list.filter((f) => f.login !== login)

export const setDays = (list: Followed[], login: string, days: number) =>
  list.map((f) => (f.login === login ? { ...f, days } : f))
