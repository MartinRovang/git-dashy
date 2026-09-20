import type { Layout } from './types'

/** The pure half of useSections (sections.tsx), kept apart so it tests without a browser. */

export const flip = (l: string[] = [], v: string) => (l.includes(v) ? l.filter((x) => x !== v) : [...l, v])

/** The saved order, minus names that no longer exist, then any new name last in `names` order. */
export const arrange = (saved: string[] | undefined, names: string[]) => [
  ...(saved || []).filter((k) => names.includes(k)),
  ...names.filter((n) => !saved?.includes(n)),
]

/** `from` taken out and put above or below `to`. */
export function moved(order: string[], from: string, to: string, after: boolean) {
  if (from === to) return order
  const rest = order.filter((k) => k !== from)
  rest.splice(rest.indexOf(to) + (after ? 1 : 0), 0, from)
  return rest
}

/** Whether a drag ended past the panel. (0,0) is a webview that reported no point, so it counts as inside. */
export const outside = (r: { left: number; right: number; top: number; bottom: number }, x: number, y: number) =>
  (x !== 0 || y !== 0) && (x < r.left || x > r.right || y < r.top || y > r.bottom)

/** Whether two layouts say the same thing. Per list, not the whole object: the server's map comes back in its own key order. */
export const same = (a: Layout | null | undefined, b: Layout | null | undefined) =>
  (['order', 'off', 'shut', 'out'] as const).every((k) => String(a?.[k] || []) === String(b?.[k] || []))

/** Which pane sections to draw: the ones with a body, plus the ones whose data is still coming.
 *
 * ponytail: what is coming is per section, not one flag for the pane. Only the GitHub detail is ever
 * in flight, and that is CHECKS; the review and database sections come from the local log, and the
 * selected row already says whether it has either, so a PR with no review never draws a bar for one.
 * `gone` is the request having given up: nothing is in flight, so nothing may claim to be loading.
 */
export function paneSections(
  order: string[],
  body: Record<string, unknown>,
  off: string[] | undefined,
  s: { detail: { pending: boolean } | null; gone: boolean; review: boolean; db: boolean },
) {
  const coming = (k: string) => {
    if (s.gone) return false
    if (k === 'checks') return !s.detail || s.detail.pending
    if (s.detail) return false // everything else is final in the first answer
    return (k === 'review' && s.review) || (k === 'database' && s.db)
  }
  return order.filter((k) => (body[k] || coming(k)) && !off?.includes(k))
}
