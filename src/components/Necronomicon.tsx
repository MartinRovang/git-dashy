// The Necronomicon: the reviewer's abilities as a book. Spells are cast once on a PR, passives (hunters) and voices
// ride along on every review. Spells are yours to write; passives and voices are built in and only switched.
//
// ponytail: everything the pages say about passives and voices comes from /api/spells. This page holds no prompt text.
import { useCallback, useEffect, useState } from 'react'
import { api, errorText, post } from '../api'
import { modalCount } from '../modals'
import type { Row } from '../types'
import { isReviewed } from '../board'
import { MessageSquare, Crosshair, Sparkles, type LucideIcon } from 'lucide-react'

// ponytail: passives with a drawing in public/sprites show it; everything else has one placeholder icon per kind
// until it gets a drawing of its own. Only this function changes when one arrives.
export const ICON: Record<'spell' | 'passive' | 'voice', LucideIcon> = { spell: Sparkles, passive: Crosshair, voice: MessageSquare }
const SPRITES = ['ponytail', 'security', 'tests', 'perf', 'humanizer']
export function Glyph({ kind, name, size = 14 }: { kind: keyof typeof ICON; name: string; size?: number }) {
  if (kind === 'passive' && SPRITES.includes(name)) return <img className="nglyph sprite" src={`/sprites/${name}.png`} width={size} height={size} alt="" />
  const I = ICON[kind]
  return <I className="nglyph" size={size} aria-hidden />
}

type Built = { name: string; about: string; prompt: string; on: boolean }
export type Book = { spells: { name: string; text: string; on: boolean }[]; passives: Built[]; voices: Built[] }

/** A spell is a review, so it starts where a review with instructions could. */
export function canCastOn(p: Row): boolean {
  return p.section === 'REVIEW REQUESTED' && !p.busy && !isReviewed(p)
}

const CHAPTERS = ['Spells', 'Passives', 'Voices'] as const

/** The book's contents, and a reload. The sidebar and the right-click menu read the same. */
export function useBook(): [Book | null, () => Promise<void>] {
  const [book, setBook] = useState<Book | null>(null)
  const load = useCallback(async () => {
    const r = await api('/api/spells')
    if (r.ok) setBook(await r.json())
  }, [])
  useEffect(() => {
    void (async () => await load())()
  }, [load])
  return [book, load]
}

export function Necronomicon({
  selected,
  onCast,
  setting,
}: {
  selected: Row | null
  onCast: (p: Row, spell: string) => void
  setting: (name: string, value: unknown) => Promise<void>
}) {
  const [book, reload] = useBook()
  const [ch, setCh] = useState(0)
  // the spell open on the right page; '' is a new one
  const [pick, setPick] = useState<string | null>(null)
  const [draft, setDraft] = useState({ name: '', text: '' })
  const [error, setError] = useState('')

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (modalCount() > 0 || /input|textarea|select/i.test((e.target as HTMLElement).tagName)) return
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      e.preventDefault()
      setCh((c) => Math.max(0, Math.min(CHAPTERS.length - 1, c + (e.key === 'ArrowRight' ? 1 : -1))))
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [])

  if (!book) return <div className="necro"><div className="ncover-msg">opening the book…</div></div>

  const open = (name: string) => {
    const s = book.spells.find((x) => x.name === name)
    setPick(name)
    setDraft({ name, text: s?.text || '' })
    setError('')
  }
  const act = async (body: Record<string, string>) => {
    const r = await post('/api/spells', body)
    if (!r.ok) {
      setError(await errorText(r))
      return false
    }
    setError('')
    await reload()
    return true
  }
  const save = async () => {
    // a rename writes the new name and removes the old one, so it does not leave two
    if (!(await act({ op: 'save', name: draft.name, text: draft.text }))) return
    if (pick && pick !== draft.name) await act({ op: 'delete', name: pick })
    setPick(draft.name)
  }
  const flip = async (key: 'spells' | 'hunter' | 'voice', list: { name: string; on: boolean }[], name: string) => {
    const on = list.filter((x) => x.on).map((x) => x.name)
    await setting(key, on.includes(name) ? on.filter((n) => n !== name) : [...on, name])
    // the book re-reads so its switches follow what the server kept
    await reload()
  }
  const current = pick === null ? null : book.spells.find((s) => s.name === pick)
  const canCast = !!selected && canCastOn(selected)

  const built = (list: Built[], key: 'hunter' | 'voice') => (
    <ul className="nbuilt">
      {list.map((b) => (
        <li key={b.name}>
          <div className="nbuilt-h">
            <Glyph kind={key === 'hunter' ? 'passive' : 'voice'} name={b.name} size={48} />
            <b>{b.name}</b>
            <button className="fld" aria-pressed={b.on} onClick={() => void flip(key, list, b.name)}>
              <span>{b.on ? 'equipped' : 'unequipped'}</span>
              <span className="sw" />
            </button>
          </div>
          <p>{b.about}</p>
          {b.prompt ? <pre className="nprompt">{b.prompt}</pre> : null}
        </li>
      ))}
    </ul>
  )

  return (
    <div className="necro scroll">
      <div className="ncover">
        <div className="nhead">
          <span className="ntitle">Necronomicon</span>
          <span className="nsub">{error ? `✗ ${error}` : selected ? `open on #${selected.number} ${selected.repo}` : 'no PR selected'}</span>
        </div>
        <div className="nspread">
          <div className="npage left">
            <h2>Chapters</h2>
            <ol className="nindex">
              {CHAPTERS.map((c, i) => (
                <li key={c} className={i === ch ? 'on' : ''}>
                  <button onClick={() => setCh(i)}>{c}</button>
                  <span className="nleader" />
                  <span>{[book.spells, book.passives, book.voices][i].length}</span>
                </li>
              ))}
            </ol>
            {ch === 0 ? (
              <>
                <h3>Spells</h3>
                <ol className="nindex">
                  {book.spells.map((s) => (
                    <li key={s.name} className={s.name === pick ? 'on' : ''}>
                      <button onClick={() => open(s.name)}>
                        <Glyph kind="spell" name={s.name} /> {s.name}
                      </button>
                      <span className="nleader" />
                      <span>{s.on ? '✦' : ''}</span>
                    </li>
                  ))}
                  <li className={pick === '' ? 'on' : ''}>
                    <button onClick={() => open('')}>+ new spell</button>
                  </li>
                </ol>
              </>
            ) : null}
          </div>
          <div className="npage right">
            {ch === 0 ? (
              pick === null ? (
                <p className="nblank">Pick a spell, or write a new one. A spell is a one-time, in-depth look at one topic, cast on one PR.</p>
              ) : (
                <section className="nspell">
                  <h2>
                    <Glyph kind="spell" name={pick || 'new'} /> {pick || 'A new spell'}
                  </h2>
                  <label>
                    name
                    <input value={draft.name} placeholder="migration-audit" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </label>
                  <label>
                    what it investigates
                    <textarea rows={10} value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
                  </label>
                  <div className="nspell-acts">
                    <button className="btn" onClick={() => void save()}>save</button>
                    {current ? (
                      <>
                        <button className="fld" aria-pressed={current.on} onClick={() => void flip('spells', book.spells, current.name)}>
                          <span>{current.on ? 'equipped' : 'unequipped'}</span>
                          <span className="sw" />
                        </button>
                        <button
                          className="btn go"
                          disabled={!canCast}
                          title={canCast ? `cast on #${selected!.number}` : 'select a PR waiting for your review'}
                          onClick={() => selected && onCast(selected, current.name)}
                        >
                          cast on {canCast ? `#${selected!.number}` : 'a PR waiting on you'}
                        </button>
                        <button
                          className="btn"
                          onClick={async () => {
                            if (await act({ op: 'delete', name: current.name })) setPick(null)
                          }}
                        >
                          delete
                        </button>
                      </>
                    ) : null}
                  </div>
                </section>
              )
            ) : ch === 1 ? (
              <section>
                <h2>Passives</h2>
                {built(book.passives, 'hunter')}
              </section>
            ) : (
              <section>
                <h2>Voices</h2>
                {built(book.voices, 'voice')}
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
