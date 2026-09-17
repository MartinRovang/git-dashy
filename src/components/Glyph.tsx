import { MessageSquare, Crosshair, Sparkles, type LucideIcon } from 'lucide-react'

// ponytail: passives with a drawing in public/sprites show it; everything else has one placeholder icon per kind
// until it gets a drawing of its own. Only this function changes when one arrives.
const ICON: Record<'spell' | 'passive' | 'voice', LucideIcon> = { spell: Sparkles, passive: Crosshair, voice: MessageSquare }
const SPRITES = ['ponytail', 'security', 'tests', 'perf', 'humanizer']
export function Glyph({ kind, name, size = 14 }: { kind: keyof typeof ICON; name: string; size?: number }) {
  if (kind === 'passive' && SPRITES.includes(name)) return <img className="nglyph sprite" src={`/sprites/${name}.png`} width={size} height={size} alt="" />
  const I = ICON[kind]
  return <I className="nglyph" size={size} aria-hidden />
}
