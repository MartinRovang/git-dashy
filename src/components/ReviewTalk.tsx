import { useEffect, useRef, useState, type ReactNode } from 'react'
import { talkControls } from '../board'
import { useReviewTalk, type Source } from '../useReviewTalk'

export type { Source }

const VERDICT: Record<string, string> = { approve: '✓ approve', request_changes: '✗ changes requested', comment: '~ comment' }

/** A saved review and the conversation about it: read it, discuss it, have it revised.
 *
 *  ponytail: ONE screen for both. A held review and a pre-review are discussed the same way and stored in
 *  the same shape; what differs is where they are read from and that only a held review is ever posted.
 *  The reading and the polling are useReviewTalk's; what is pressable is talkControls'. This only draws. */
export function ReviewTalk({
  source,
  onFlash,
  onText,
  actions,
}: {
  source: Source
  onFlash: (s: string) => void
  onText?: (t: string) => void
  /** What the review itself can be done with — post and drop, or copy — at the right of the one bar. */
  actions?: ReactNode
}) {
  const { view: v, failed, act } = useReviewTalk(source, onFlash, onText)
  const [draft, setDraft] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const held = source.kind === 'held'
  const busy = !!v?.talk?.busy
  const can = talkControls(v?.talk ?? null, draft)
  // follow the conversation as it grows, but not on opening: the review is what you open it to read
  const seen = useRef<number | null>(null)
  const turns = v?.talk?.thread.length ?? null
  useEffect(() => {
    if (turns === null) return
    if (seen.current !== null && (turns > seen.current || busy)) end.current?.scrollIntoView({ block: 'end' })
    seen.current = turns
  }, [turns, busy])

  const send = async () => {
    const text = draft.trim()
    if (!can.send) return
    if (await act({ op: 'discuss', text })) setDraft('')
  }

  if (!v) return <p>{failed || 'reading…'}</p>
  const t = v.talk
  return (
    <div className="held">
      <div className="talkbody scroll">
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
              <button className="btn go" disabled={!can.decide} onClick={() => void act({ op: 'accept' }, 'the revision replaces the review')}>
                accept the revision
              </button>
              <button className="btn" disabled={!can.decide} onClick={() => void act({ op: 'keep' }, 'kept the original')}>
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
      </div>

      {/* pinned under the scrolling part, so the box is in reach however long the review or the
          conversation gets */}
      <div className="talkfoot">
        {!t ? (
          <div className="note">this pre-review was written before discussions were saved; run it again to discuss it</div>
        ) : t.cannotDiscuss ? (
          <div className="note">{t.cannotDiscuss}</div>
        ) : (
          <textarea
            className="talkin"
            value={draft}
            disabled={!can.type}
            placeholder="Ask about this review, or tell it what it got wrong."
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                void send()
              }
            }}
          />
        )}
        {/* ponytail: ONE bar. The conversation's two buttons sat in a row of their own over the modal's
            footer, so the screen had five buttons in two rows and a third for Esc, which closes every modal
            anyway. Talking is on the left, what happens to the review on the right, and only the
            irreversible one is filled in. */}
        <div className="talkbar">
          {t && !t.cannotDiscuss ? (
            <>
              {/* a revision is asked for separately: talking should not quietly rewrite the review */}
              <button
                className="lnk"
                disabled={!can.revise}
                onClick={() => void act({ op: 'revise' })}
                title={t.thread.length ? 'ask the agent to write the review again, taking this conversation into account' : 'discuss it first'}
              >
                revise the review
              </button>
              <span className="sp" />
              <button className="btn" disabled={!can.send} onClick={() => void send()} title="Ctrl+Enter">
                <kbd>⌃⏎</kbd>send
              </button>
            </>
          ) : (
            <span className="sp" />
          )}
          {actions ? (
            <>
              <span className="sep" />
              {actions}
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}
