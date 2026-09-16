// The Necronomicon: memory's main points as a book. Learn (daily on its own, or on the button) writes
// the points; the server ranks them by how often their facts come up in reviews and by your deranks.
// General knowledge fills the left page, each repo is a chapter on the right. Points that fade sit in
// lighter ink, and the ones in the depths only show when you dig.
//
// ponytail: tiers and scores come from the server (necro.rs). This page draws them and holds no rule.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText, post } from '../api'
import { close, modalCount, open } from '../modals'
import type { Foot } from '../modals'
import { age, elapsed } from '../tokens'
import { useNow } from '../usePoll'

const ago = (secs: number) => {
  const a = age(new Date(secs * 1000).toISOString())
  return a === 'now' ? 'just now' : `${a} ago`
}

const BOOK = 'M-.5,-.6H.5V.6H-.5ZM-.3,-.48H-.22V.22H-.3ZM-.3,.34H.38V.46H-.3Z'

/** The footer's reminder: a book and the time until the Necronomicon wants learning again, then "learn".
 *  Pressing it opens the book, where the Learn button does the glowing. Nothing learns by itself; this only asks. */
export function NextLearn({ at, running, onOpen }: { at: number; running: boolean; onOpen: () => void }) {
  const left = Math.max(0, at - useNow(1000) / 1000)
  const h = Math.floor(left / 3600)
  const m = Math.floor((left % 3600) / 60)
  const sec = Math.floor(left % 60)
  const due = !left && !running
  return (
    <button className={`nnext${due ? ' due' : ''}`} title={due ? 'the Necronomicon wants to learn: open it' : 'until the Necronomicon wants to learn again'} onClick={onOpen}>
      {/* a closed book, the same glyph the graph draws for a repo */}
      <svg viewBox="-0.6 -0.6 1.2 1.2" aria-hidden="true">
        <path fillRule="evenodd" d={BOOK} />
      </svg>
      {running ? 'learning…' : due ? 'learn' : h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`}
    </button>
  )
}

/** Learn: grey while the reminder counts down (gold on hover, it still works), glowing once the day is up. Its own component so the book does not re-render every second. */
function LearnButton({ at, running, elapsedSecs, onClick }: { at: number; running: boolean; elapsedSecs: number; onClick: () => void }) {
  const now = useNow(1000) / 1000
  const due = !running && now >= at
  return (
    <button className={`nlearn-btn${due ? ' due' : running ? '' : ' wait'}`} disabled={running} title={due ? 'a day has passed: learn again' : undefined} onClick={onClick}>
      {running ? (
        <>
          <span className="spinner" />
          learning… {elapsed(elapsedSecs)}
        </>
      ) : (
        <>
          {/* a skull: cranium with eye sockets and a nose cut out (evenodd), and a jaw with teeth gaps */}
          <svg className="nskull" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fillRule="evenodd"
              d="M12 2C6.9 2 3 5.7 3 10.4c0 2.8 1.3 5 3.5 6.3V19h11v-2.3c2.2-1.3 3.5-3.5 3.5-6.3C21 5.7 17.1 2 12 2ZM8.2 9.2a2.1 2.1 0 1 0 0 4.2 2.1 2.1 0 0 0 0-4.2Zm7.6 0a2.1 2.1 0 1 0 0 4.2 2.1 2.1 0 0 0 0-4.2ZM12 13.6l-1.4 2.4h2.8Z"
            />
            <path fillRule="evenodd" d="M7 20h10v2.4H7ZM9.4 20h1v2.4h-1Zm4.2 0h1v2.4h-1Z" />
          </svg>
          Learn
        </>
      )}
    </button>
  )
}

type Point = { scope: string; text: string; hits: number; down: number; score: number; tier: 'open' | 'faded' | 'depths' }
type Whisper = { repo: string | null; n: number; fact: string; kind: string }
type Tome = {
  learnedAt: number
  nextAt: number
  points: Point[]
  job: { running: boolean; elapsed?: number; error?: string }
  promoteAt: number
  learning: Whisper[]
}

export function Necronomicon() {
  const [tome, setTome] = useState<Tome | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [deep, setDeep] = useState(false)
  // the chapter open on the right page; one repo a page, turned with the corner arrows or ← →
  const [ch, setCh] = useState(0)
  // ponytail: the keys press the page-turn buttons, which already know when there is no page to turn to
  const turn = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (modalCount() > 0 || /input|textarea|select/i.test((e.target as HTMLElement).tagName)) return
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      const button = turn.current?.querySelectorAll('button')[e.key === 'ArrowRight' ? 1 : 0]
      if (!button) return
      e.preventDefault()
      button.click()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [])

  const load = useCallback(async () => {
    const r = await api('/api/necronomicon')
    if (r.ok) {
      setTome(await r.json())
      setError('')
    } else setError(await errorText(r))
  }, [])

  useEffect(() => {
    void (async () => await load())()
  }, [load])

  // while a learn runs, watch it land
  const running = !!tome?.job.running
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => void load(), 2000)
    return () => clearInterval(t)
  }, [running, load])

  const act = async (body: Record<string, string>) => {
    const r = await post('/api/necronomicon', body)
    if (!r.ok) setError(await errorText(r))
    await load()
  }

  const askLearn = () => {
    const go = () => {
      close(m)
      void act({ op: 'learn' })
    }
    const m = open({
      title: 'Learn',
      body: () => (
        <>
          <div>Learn reads every fact in memory, yours and your teams', and asks the model to write down the main points: a few for general, and a few for each repo.</div>
          <div style={{ margin: '8px 0 8px 12px' }}>
            · one model call on your reviewer model, usually a minute or two
            <br />· the points replace the book's current ones, each citing the facts it came from
            <br />· a reworded point keeps its rank and your deranks
            <br />· memory itself is only read, never changed
          </div>
          <div style={{ color: 'var(--dim)' }}>
            Points rank by how often a review proposes their facts again, and fade when nothing does. It never runs by itself: a day after a learn, the book in the footer reminds you and this button glows.
          </div>
        </>
      ),
      foot: [['y', 'learn now', go, 'go'], ['n', 'not now', () => close(m)]] as Foot[],
    })
    m.keys = { Enter: go, Escape: () => close(m) }
  }

  if (!tome) return <div className="necro">{error ? <div className="ncover-msg">✗ {error}</div> : <div className="ncover-msg">opening the book…</div>}</div>

  const q = query.trim().toLowerCase()
  const hit = (scope: string, text: string) => !q || (scope || 'general').toLowerCase().includes(q) || text.toLowerCase().includes(q)
  const found = tome.points.filter((p) => hit(p.scope, p.text))
  const shown = found.filter((p) => deep || p.tier !== 'depths')
  const buried = found.length - found.filter((p) => p.tier !== 'depths').length
  const general = shown.filter((p) => !p.scope)
  // chapters in the order of their best point: the points arrive best first
  const chapters = new Map<string, Point[]>()
  for (const p of shown.filter((p) => p.scope)) chapters.set(p.scope, [...(chapters.get(p.scope) || []), p])
  const books = [...chapters]
  // the right page turns through every chapter, then the whispers as a page of their own
  const at = Math.max(0, Math.min(ch, books.length))
  const onWhispers = at === books.length
  const whispers = tome.learning.filter((w) => hit(w.repo || '', w.fact)).sort((a, b) => b.n - a.n)

  const line = (p: Point) => (
    <li key={p.scope + p.text} className={`npoint ${p.tier}`}>
      <span className="ntext">{p.text}</span>
      <span className="nmeta">
        {p.hits ? <span title={`its facts came up ${p.hits} times in reviews`}>✦{p.hits}</span> : null}
        {p.tier !== 'open' ? (
          <button title={p.down ? 'raise it back' : 'bring it back to the open page'} onClick={() => void act({ op: 'up', scope: p.scope, text: p.text })}>
            ▴
          </button>
        ) : null}
        {p.tier !== 'depths' ? (
          <button title="derank: let it sink" onClick={() => void act({ op: 'down', scope: p.scope, text: p.text })}>
            ▾
          </button>
        ) : null}
      </span>
    </li>
  )

  return (
    <div className="necro scroll">
      <div className="ncover">
        <div className="nhead">
          <span className="ntitle">Necronomicon</span>
          <span className="nsub">
            {tome.learnedAt ? `last learned ${ago(tome.learnedAt)}` : 'never learned'}
            {tome.job.error && !running ? ` · ✗ ${tome.job.error}` : ''}
            {error ? ` · ✗ ${error}` : ''}
          </span>
          <input placeholder="search the pages" value={query} onChange={(e) => {
              setQuery(e.target.value)
              setCh(0)
            }} />
          <LearnButton at={tome.nextAt} running={running} elapsedSecs={tome.job.elapsed || 0} onClick={askLearn} />
        </div>

        <div className="nspread">
          <div className="npage left">
            <h2>Of All Things</h2>
            {general.length ? <ol>{general.map(line)}</ol> : <p className="nblank">{tome.points.length ? 'nothing here' + (q ? ' matches' : '') : 'The pages are blank. Learn reads memory and writes down what matters.'}</p>}

            <h3>Chapters</h3>
            <ol className="nindex">
              {books.map(([scope, points], i) => (
                <li key={scope} className={i === at ? 'on' : ''}>
                  <button onClick={() => setCh(i)}>{scope}</button>
                  <span className="nleader" />
                  <span>{points.length}</span>
                </li>
              ))}
              <li className={`nindex-whispers${onWhispers ? ' on' : ''}`}>
                <button onClick={() => setCh(books.length)}>Whispers</button>
                <span className="nleader" />
                <span>{whispers.length}</span>
              </li>
            </ol>
          </div>

          <div className="npage right">
            {onWhispers ? (
              <section>
                <h2>Whispers</h2>
                <p className="nnote">not yet facts: each needs {tome.promoteAt} reviews to see it</p>
                {whispers.length ? (
                  <ul className="nwhispers">
                    {whispers.map((w, i) => (
                      <li key={i}>
                        <span className="nwho">
                          {w.kind === 'self' ? 'self' : `${w.n}/${tome.promoteAt}`} · {w.repo || 'general'}
                        </span>
                        {w.fact}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="nblank">silence{q ? ' matches' : ''}</p>
                )}
              </section>
            ) : (
              <section>
                <h2>{books[at][0]}</h2>
                <ol>{books[at][1].map(line)}</ol>
              </section>
            )}
            {!onWhispers && (buried || deep) ? (
              <button className="ndig" onClick={() => setDeep((v) => !v)}>
                {deep ? '✧ close the depths' : `✧ dig into the depths (${buried})`}
              </button>
            ) : null}
            {books.length ? (
              <div className="nturn" ref={turn}>
                <button aria-label="previous page" disabled={at === 0} onClick={() => setCh(at - 1)}>
                  ◂
                </button>
                <span>{onWhispers ? 'whispers' : `chapter ${at + 1} of ${books.length}`}</span>
                <button aria-label="next page" disabled={onWhispers} onClick={() => setCh(at + 1)}>
                  ▸
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
