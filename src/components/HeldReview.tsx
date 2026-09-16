import { useEffect, useRef, useState } from 'react'
import { api, errorText, post } from '../api'
import type { HeldReview as Held } from '../types'

const VERDICT: Record<string, string> = { approve: '✓ approve', request_changes: '✗ changes requested', comment: '~ comment' }

/** A held review, and the conversation about it: read, discuss, revise, then post or drop.
 *
 *  ponytail: its own polling, not the board's. The agent answers in minutes and the board refreshes on a
 *  much longer interval, so a turn would sit unseen until the next tick; while `busy` this asks every
 *  1.5s and stops the moment the turn lands. */
export function HeldReview({ repo, number, onFlash }: { repo: string; number: number; onFlash: (s: string) => void }) {
  const [h, setH] = useState<Held | null>(null)
  const [draft, setDraft] = useState('')
  const [failed, setFailed] = useState('')
  const end = useRef<HTMLDivElement>(null)

  const load = async () => {
    const r = await api(`/api/posting?repo=${encodeURIComponent(repo)}&number=${number}`)
    if (!r.ok) return setFailed(await errorText(r))
    const d = await r.json()
    setH(d.held)
    if (!d.held) setFailed('nothing is waiting on this PR any more')
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => void load(), [repo, number])
  useEffect(() => {
    if (!h?.busy) return
    const id = setInterval(() => void load(), 1500)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [h?.busy])
  useEffect(() => end.current?.scrollIntoView({ block: 'nearest' }), [h?.thread.length, h?.busy])

  const act = async (body: Record<string, unknown>, ok?: string) => {
    const r = await post('/api/posting', { repo, number, ...body })
    if (!r.ok) onFlash(`✗ ${await errorText(r)}`)
    else if (ok) onFlash(ok)
    await load()
    return r.ok
  }
  const send = async () => {
    const text = draft.trim()
    if (!text || h?.busy) return
    if (await act({ op: 'discuss', text })) setDraft('')
  }

  if (!h) return <p>{failed || 'reading…'}</p>
  return (
    <div className="held">
      {/* the verdict was written against a head that is no longer the one on the board, so posting it now
          puts an old reading against new commits */}
      {h.moved ? <div className="note">⚠ New commits were pushed after this review was written. It describes the older ones.</div> : null}
      {h.instructions ? (
        <div className="asked" title="private: kept on this machine, never posted">
          <b>you asked it to</b>
          <span>{h.instructions}</span>
        </div>
      ) : null}

      <div className="verdict">
        <b>{VERDICT[h.verdict] || h.verdict}</b>
        <span>what posts</span>
      </div>
      <pre>{h.body}</pre>

      {h.proposed ? (
        <div className="proposal">
          <div className="verdict">
            <b>{VERDICT[h.proposed.verdict] || h.proposed.verdict}</b>
            <span>revised — not posted unless you accept it</span>
          </div>
          <pre>{h.proposed.body}</pre>
          <div className="row">
            <button className="btn go" disabled={h.busy} onClick={() => void act({ op: 'accept' }, 'the revision replaces the review')}>
              accept the revision
            </button>
            <button className="btn" disabled={h.busy} onClick={() => void act({ op: 'keep' }, 'kept the original')}>
              keep the original
            </button>
          </div>
        </div>
      ) : null}

      <div className="thread">
        {h.thread.map((t, i) => (
          <div key={i} className={`turn ${t.who}`}>
            <b>{t.who === 'you' ? 'you' : t.who === 'agent' ? 'agent' : 'failed'}</b>
            <p>{t.text}</p>
          </div>
        ))}
        {h.busy ? <div className="turn agent working"><b>agent</b><p className="shimtext">working…</p></div> : null}
        <div ref={end} />
      </div>

      {h.cannotDiscuss ? (
        <div className="note">{h.cannotDiscuss}</div>
      ) : (
        <div className="ask">
          <textarea
            value={draft}
            disabled={h.busy}
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
            <button className="btn go" disabled={h.busy || !draft.trim()} onClick={() => void send()}>
              send
            </button>
            {/* a revision is asked for separately: talking should not quietly rewrite what posts */}
            <button className="btn" disabled={h.busy || !!h.proposed || !h.thread.length} onClick={() => void act({ op: 'revise' })}
              title={h.thread.length ? 'ask the agent to write the review again, taking this conversation into account' : 'discuss it first'}>
              revise the review
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
