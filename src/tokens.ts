// The design tokens and pure helpers the vanilla gui.html kept at the top of its script. No DOM, so
// components and tests can share them.
import type { Pr } from './types'

// ponytail: the fill and the border are mixed from the same var as the text, so a theme swap moves
// all three together. The hardcoded rgba they replaced stayed the old palette's neon under a theme.
const wash = (v: string, pct: number) => `color-mix(in srgb, var(${v}) ${pct}%, transparent)`
export const PALETTE: Record<string, { fg: string; bg: string; border: string }> = {
  approved: { fg: 'var(--green)', bg: wash('--green', 10), border: wash('--green', 28) },
  changes: { fg: 'var(--red)', bg: wash('--red', 10), border: wash('--red', 28) },
  commented: { fg: 'var(--amber)', bg: wash('--amber', 10), border: wash('--amber', 28) },
  awaiting: { fg: 'var(--cyan)', bg: wash('--cyan', 10), border: wash('--cyan', 28) },
  running: { fg: 'var(--cyan)', bg: wash('--cyan', 10), border: wash('--cyan', 28) },
  error: { fg: 'var(--red)', bg: wash('--red', 8), border: wash('--red', 22) },
  idle: { fg: 'var(--dim)', bg: wash('--dim', 8), border: 'var(--edge)' },
}
// ponytail: the theme's own accents, so avatars follow a theme swap instead of staying neon.
const AVATARS = ['var(--pink)', 'var(--cyan)', 'var(--green)', 'var(--amber)', 'var(--violet)', 'var(--ink3)']
export const SECTION_TONE: Record<string, string> = {
  MINE: 'var(--pink)',
  'REVIEW REQUESTED': 'var(--cyan)',
  ASSIGNED: 'var(--amber)',
  REVIEWED: 'var(--dim)',
}
export const SECTION_HINT: Record<string, string> = {
  MINE: 'your open PRs',
  'REVIEW REQUESTED': 'waiting on you',
  ASSIGNED: 'owned by you',
  REVIEWED: 'recently done',
}
export const FINDING_TONE: Record<string, string> = { blocking: 'var(--red)', note: 'var(--amber)', nit: 'var(--cyan)' }
export const CHECK_TONE: Record<string, string> = {
  ok: 'var(--green)',
  err: 'var(--red)',
  fail: 'var(--red)',
  run: 'var(--amber)',
  skip: 'var(--dim)',
  dim: 'var(--dim)',
}
export const MARK: Record<string, string> = { blocking: '◆', note: '◇', nit: '·' }
export const CONTEXTS = [3, 8, 0]
export const SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

export function avatar(name: string): string {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 997
  return AVATARS[h % AVATARS.length]
}

export function age(iso: string): string {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  for (const [u, d] of [['d', 86400], ['h', 3600], ['m', 60]] as const) if (s >= d) return Math.floor(s / d) + u
  return 'now'
}

export const every = (v: number) => (v < 60 || v % 60 ? `${v}s` : `${v / 60}m`)
export const elapsed = (s: number) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m`)

export const when = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })

// ponytail: the status strings are already human — classify by their leading glyph, do not re-derive.
export function tone(s: string | undefined | null): string {
  if (!s) return ''
  if (s.startsWith('✓')) return 'approved'
  if (s.startsWith('✗')) return 'changes'
  if (s.startsWith('~')) return 'commented'
  if (s.startsWith('·') || s.startsWith('↻') || s.startsWith('●')) return 'awaiting'
  if (/^(error|no )/i.test(s)) return 'error'
  return ''
}

/** What the row's chip says: what THIS session did wins over what was fetched. */
export function rowState(p: Pr): { key: string; label: string } {
  if (p.busy) {
    const el = p.since ? Math.max(0, Math.floor(Date.now() / 1000 - p.since)) : 0
    return { key: 'running', label: `${(p.review || 'reviewing').replace(/\.\.\.$/, '')}… ${elapsed(el)}` }
  }
  for (const s of [p.review, p.status, p.prev]) {
    const t = tone(s)
    if (t) return { key: t, label: s.replace(/^[✓✗~·↻●]\s*/, '') }
  }
  return { key: 'idle', label: 'no review' }
}
