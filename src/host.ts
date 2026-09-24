// ponytail: inside icecream, which injects this before the page loads. Standalone there is none and
// nothing renders. Checked, not trusted: only a icecream switch link and an inline icon get through.
export type HostSwitch = { href: string; icon: string; title: string }

export function hostSwitch(): HostSwitch | null {
  const s = (globalThis as { __ICECREAM__?: { switch?: Partial<Record<keyof HostSwitch, unknown>> } }).__ICECREAM__?.switch
  if (!s || typeof s.href !== 'string' || typeof s.icon !== 'string' || typeof s.title !== 'string') return null
  if (!s.href.startsWith('icecream://switch/') || !s.icon.startsWith('data:image/')) return null
  return { href: s.href, icon: s.icon, title: s.title }
}
