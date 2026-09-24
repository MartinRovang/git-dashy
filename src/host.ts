// ponytail: inside neo-suite, which injects this before the page loads. Standalone there is none and
// nothing renders. Checked, not trusted: only a neo-suite switch link and an inline icon get through.
export type HostSwitch = { href: string; icon: string; title: string }

export function hostSwitch(): HostSwitch | null {
  const s = (globalThis as { __NEO_SUITE__?: { switch?: Partial<Record<keyof HostSwitch, unknown>> } }).__NEO_SUITE__?.switch
  if (!s || typeof s.href !== 'string' || typeof s.icon !== 'string' || typeof s.title !== 'string') return null
  if (!s.href.startsWith('neo-suite://switch/') || !s.icon.startsWith('data:image/')) return null
  return { href: s.href, icon: s.icon, title: s.title }
}
