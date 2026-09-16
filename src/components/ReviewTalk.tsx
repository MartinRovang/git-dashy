import { useEffect, useRef, useState } from 'react'
import { api, errorText, post } from '../api'
import type { Talk } from '../types'

const VERDICT: Record<string, string> = { approve: '✓ approve', request_changes: '✗ changes requested', comment: '~ comment' }

/** Which saved review this screen is about. A held review is keyed by repo and number, the way ~/.prs_held
 *  names it; a pre-review by its PR's URL, the way /api/prereview finds it. */
export type Source = { kind: 'held'; repo: string; number: number } | { kind: 'pre'; url: string }

/** What the screen draws, whichever store it came from. */
type View = { text: string; verdict: string; moved: boolean; talk: Talk | null }

async function fetchView(src: Source): Promise<View | string> {
  const r =
    src.kind === 'held'
      ? await api(`/api/posting?repo=${encodeURIComponent(src.repo)}&number=${src.number}`)
      : await api(`/api/prereview?url=${encodeURIComponent(src.url)}`)
  if (!r.ok) return errorText(r)
  const d = await r.json()
  if (src.kind === 'held') {
    if (!d.held) return 'nothing is waiting on this PR any more'
    return { text: d.held.body, verdict: d.held.verdict, moved: d.held.moved, talk: d.held.talk }
  }
  return { text: d.text, verdict: d.talk?.verdict || '', moved: d.moved, talk: d.talk }
}

/** A saved review and the conversation about it: read it, discuss it, have it revised.
 *
 *  ponytail: ONE screen for both. A held review and a pre-review are discussed the same way and stored in
 *  the same shape; what differs is where they are read from and that only a held review is ever posted.
 *  ponytail: its own polling, not the board's. The agent answers in minutes and the board refreshes on a
 *  much longer interval; while `busy` this asks every 1.5s and stops the moment the turn lands. */
export function ReviewTalk({ source, onFlash, onText }: { source: Source; onFlash: (s: string) => void; onText?: (t: string) => void }) {
  const [v, setV] = useState<View | null>(null)
  const [failed, setFailed] = useState('')
  const [draft, setDraft] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const held = source.kind === 'held'
  const key = held ? `${source.repo}#${source.number}` : source.url

  const load = async () => {
    const got = await fetchView(source)
    if (typeof got === 'string') return setFailed(got)
    setV(got)
    onText?.(got.text)
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => void load(), [key])
  const busy = !!v?.talk?.busy
  useEffect(() => {
    if (!busy) return
    const id = setInterval(() => void load(), 1500)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, key])
  useEffect(() => end.current?.scrollIntoView({ block: 'nearest' }), [v?.talk?.thread.length, busy])

  const act = async (body: Record<string, unknown>, ok?: string) => {
    const where = held ? { repo: source.repo, number: source.number } : { url: source.url }
    const r = await post(held ? '/api/posting' : '/api/prereview', { ...where, ...body })
    if (!r.ok) onFlash(`✗ ${await errorText(r)}`)
    else if (ok) onFlash(ok)
    await load()
    return r.ok
  }
  const send = async () => {
    const text = draft.trim()
    if (!text || busy) return
    if (await act({ op: 'discuss', text })) setDraft('')
  }

  if (!v) return <p>{failed || 'reading…'}</p>
  const t = v.talk
  return (
    <div className="held">
      {v.moved ? (
        <div className="note">
          ⚠ New commits were pushed after this review was written. It describes the older ones.
        </div>
      ) : null}
      {t?.instructions ? (
        <div className="asked" title="private: kept on this machine, never posted">
          <b>you asked it to</b>
          <span>{t.instructions}</span>
        </div>
      ) : null}

      {held ? (
        <div className="verdict">
          <b>{VERDICT[v.verdict] || v.verdict}</b>
          <span>what posts</span>
        </div>
      ) : null}
      <pre>{v.text}</pre>

      {t?.proposed ? (
        <div className="proposal">
          <div className="verdict">
            <b>{VERDICT[t.proposed.verdict] || t.proposed.verdict}</b>
            <span>{held ? 'revised — not posted unless you accept it' : 'revised — replaces the pre-review if you accept it'}</span>
          </div>
          <pre>{t.proposed.body}</pre>
          <div className="row">
            <button className="btn go" disabled={busy} onClick={() => void act({ op: 'accept' }, 'the revision replaces the review')}>
              accept the revision
            </button>
            <button className="btn" disabled={busy} onClick={() => void act({ op: 'keep' }, 'kept the original')}>
              keep the original
            </button>
          </div>
        </div>
      ) : null}

      {t ? (
        <div className="thread">
          {t.thread.map((turn, i) => (
            <div key={i} className={`turn ${turn.who}`}>
              <b>{turn.who === 'error' ? 'failed' : turn.who}</b>
              <p>{turn.text}</p>
            </div>
          ))}
          {busy ? (
            <div className="turn agent working">
              <b>agent</b>
              <p className="shimtext">working…</p>
            </div>
          ) : null}
          <div ref={end} />
        </div>
      ) : null}

      {!t ? (
        <div className="note">this pre-review was written before discussions were saved; run it again to discuss it</div>
      ) : t.cannotDiscuss ? (
        <div className="note">{t.cannotDiscuss}</div>
      ) : (
        <div className="ask">
          <textarea
            value={draft}
            disabled={busy}
            placeholder="Ask about this review, or tell it what it got wrong. Ctrl+Enter sends."
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                void send()
              }
            }}
          />
          <div className="row">
            <button className="btn go" disabled={busy || !draft.trim()} onClick={() => void send()}>
              send
            </button>
            {/* a revision is asked for separately: talking should not quietly rewrite the review */}
            <button
              className="btn"
              disabled={busy || !!t.proposed || !t.thread.length}
              onClick={() => void act({ op: 'revise' })}
              title={t.thread.length ? 'ask the agent to write the review again, taking this conversation into account' : 'discuss it first'}
            >
              revise the review
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
