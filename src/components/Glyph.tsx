import { MessageSquare, Crosshair, Sparkles, type LucideIcon } from 'lucide-react'

// ponytail: anything with a drawing in public/sprites shows it; the rest have one placeholder icon per kind
// until they get a drawing of their own. Only SPRITES changes when one arrives.
const ICON: Record<'spell' | 'passive' | 'voice', LucideIcon> = { spell: Sparkles, passive: Crosshair, voice: MessageSquare }
const SPRITES = ['ponytail', 'security', 'tests', 'perf', 'humanizer', 'spaghetti', 'review', 'caveman', 'bot', 'auth-check', 'test-gaps', 'migration-audit']
// the audit spell borrows the spaghetti hunter's drawing, tinted blue so the two are told apart
export function Glyph({ kind, name, size = 14 }: { kind: keyof typeof ICON; name: string; size?: number }) {
  if (name === 'spaghetti-audit') return <img className="nglyph sprite tint" src="/sprites/spaghetti.png" width={size} height={size} alt="" />
  if (SPRITES.includes(name)) return <img className="nglyph sprite" src={`/sprites/${name}.png`} width={size} height={size} alt="" />
  const I = ICON[kind]
  return <I className="nglyph" size={size} aria-hidden />
}
