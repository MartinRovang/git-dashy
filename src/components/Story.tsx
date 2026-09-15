import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useFloatBox } from '../float'
import type { Followed } from '../stories'

type Got = { summary: string; prs: { repo: string; number: number; title: string }[] }

/** One followed user's floating card: what they have been on for the last `days`, in the model's words. */
export function Story({ f, i, onDays, onClose }: { f: Followed; i: number; onDays: (d: number) => void; onClose: () => void }) {
  const { box, el, drag, style } = useFloatBox(
    `story:${f.login}`,
    () => ({ x: window.innerWidth - 360 - i * 28, y: 70 + i * 28, w: 340, h: 230, max: false }),
    '.iconbtn, select',
  )
  const [got, setGot] = useState<Got | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = (fresh: boolean) => {
    setBusy(true)
    setErr('')
    api(`/api/story?login=${encodeURIComponent(f.login)}&days=${f.days}${fresh ? '&fresh=1' : ''}`)
      .then(async (r) => (r.ok ? setGot(await r.json()) : setErr(await errorText(r))))
      .catch(() => setErr('server gone'))
      .finally(() => setBusy(false))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(false), [f.login, f.days])

  return (
    <div ref={el} className={`fw story${box.max ? ' max' : ''}`} style={style} role="dialog" aria-label={`${f.login}'s story`}>
      <div className="bar" title="drag to move, double-click to maximize" {...drag}>
        <b>{f.login}</b>
        <div style={{ flex: 1 }} />
        <select value={f.days} onChange={(e) => onDays(Number(e.target.value))} title="timespan">
          {[1, 3, 7].map((d) => (
            <option key={d} value={d}>
              {d === 7 ? '1w' : `${d}d`}
            </option>
          ))}
        </select>
        <button className="iconbtn" onClick={() => load(true)} disabled={busy} title="ask again">
          ⟳
        </button>
        <button className="iconbtn" onClick={onClose} title="unfollow">
          ✕
        </button>
      </div>
      <div className="keys scroll">
        {err ? <p style={{ color: 'var(--red)' }}>✗ {err}</p> : busy && !got ? <p style={{ color: 'var(--dim2)' }}>asking the model…</p> : <p>{got?.summary}</p>}
        {got?.prs.length ? (
          <details>
            <summary>
              {got.prs.length} PR{got.prs.length === 1 ? '' : 's'}
            </summary>
            {got.prs.map((p) => (
              <div className="krow" key={`${p.repo}#${p.number}`}>
                <s>
                  {p.repo}#{p.number} {p.title}
                </s>
              </div>
            ))}
          </details>
        ) : null}
      </div>
    </div>
  )
}
