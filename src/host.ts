// ponytail: inside icecream, which injects this before the page loads. Standalone there is none and
// nothing renders. Checked, not trusted: only an icecream switch link with an inline icon gets through.
export type HostApp = { id: string; name: string; icon: string; href: string; key: string; current: boolean }

const ok = (a: Partial<Record<keyof HostApp, unknown>>): a is HostApp =>
  typeof a.id === 'string' &&
  typeof a.name === 'string' &&
  typeof a.key === 'string' &&
  typeof a.current === 'boolean' &&
  typeof a.href === 'string' &&
  a.href.startsWith('icecream://switch/') &&
  typeof a.icon === 'string' &&
  a.icon.startsWith('data:image/')

export function hostApps(): HostApp[] | null {
  const apps = (globalThis as { __ICECREAM__?: { apps?: unknown } }).__ICECREAM__?.apps
  if (!Array.isArray(apps)) return null
  const good = apps.filter((a) => a && typeof a === 'object' && ok(a))
  return good.length ? good : null
}
