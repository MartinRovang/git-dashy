// The Necronomicon: the reviewer's abilities as a book. Spells are cast once on a PR, passives (hunters) and voices
// ride along on every review. Spells are markdown files in ~/.prs_spells; passives and voices are built in. The book
// only equips them.
//
// ponytail: everything the pages say comes from /api/spells. This page holds none of that text.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { modalCount } from '../modals'
import { Check } from 'lucide-react'
import { Glyph } from './Glyph'

type Card = { name: string; about: string; on: boolean }
export type Book = { spells: Card[]; passives: Card[]; voices: Card[] }

const CHAPTERS = [
  ['Spells', 'spells', 'spell'],
  ['Passives', 'hunter', 'passive'],
  ['Voices', 'voice', 'voice'],
] as const

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

/** The equip mark: an empty box, or a ticked one. */
function Tick({ on }: { on: boolean }) {
  return <span className={`ntick${on ? ' on' : ''}`}>{on ? <Check size={14} strokeWidth={3} aria-hidden /> : null}</span>
}

export function Necronomicon({ setting }: { setting: (name: string, value: unknown) => Promise<void> }) {
  const [book, reload] = useBook()
  const [ch, setCh] = useState(0)

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

  const lists = [book.spells, book.passives, book.voices]
  const [title, key, kind] = CHAPTERS[ch]
  const list = lists[ch]
  const flip = async (name: string) => {
    const on = list.filter((x) => x.on).map((x) => x.name)
    await setting(key, on.includes(name) ? on.filter((n) => n !== name) : [...on, name])
    // the book re-reads so its ticks follow what the server kept
    await reload()
  }

  return (
    <div className="necro scroll">
      <div className="ncover">
        <div className="nhead">
          <span className="ntitle">Necronomicon</span>
        </div>
        <div className="nspread">
          <div className="npage left">
            <h2>Chapters</h2>
            <ol className="nindex">
              {CHAPTERS.map(([c], i) => (
                <li key={c} className={i === ch ? 'on' : ''}>
                  <button onClick={() => setCh(i)}>{c}</button>
                  <span className="nleader" />
                  <span>{lists[i].length}</span>
                </li>
              ))}
            </ol>
            {ch === 0 ? (
              <p className="nblank">
                A spell is a one-time, in-depth look at one topic, cast on any PR. Its result stays private until you post it. Each is a markdown file in ~/.prs_spells; add one there
                and it shows here. Equipped spells can be cast from the sidebar and a PR's right-click menu.
              </p>
            ) : null}
          </div>
          <div className="npage right">
            <section>
              <h2>{title}</h2>
              {list.length ? (
                <ul className="nbuilt">
                  {list.map((b) => (
                    <li key={b.name} className={b.on ? 'on' : ''}>
                      <button className="ncard" aria-pressed={b.on} title={b.on ? 'equipped: click to take it off' : 'click to equip'} onClick={() => void flip(b.name)}>
                        <Tick on={b.on} />
                        <Glyph kind={kind} name={b.name} size={96} />
                        <b>{b.name}</b>
                        <p>{b.about}</p>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="nblank">{ch === 0 ? 'No spells yet. Put a markdown file in ~/.prs_spells.' : `No ${title.toLowerCase()}.`}</p>
              )}
            </section>
          </div>
        </div>
      </div>
    </div>
  )
}
