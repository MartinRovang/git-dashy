// The curses panel() as React dialogs. The vanilla kept a stack and painted innerHTML; here a tiny
// external store drives one ModalHost, and confirm/prompt/notice/viewer/editor/picker stay promise-based
// so the async actions that use them read the same as gui.html.
import type { ReactNode } from 'react'
import { useEffect, useSyncExternalStore } from 'react'
import { fuzzy, isLogin } from './stories'

export type Foot = [key: string, label: string, fn: () => void, cls?: string]

export type Modal = {
  id: number
  title: string
  sub?: string
  wide?: boolean
  dismiss?: boolean
  body: () => ReactNode
  foot?: Foot[]
  focus?: string
  keys?: Record<string, () => void>
  onClose?: () => void
}

let stack: Modal[] = []
let version = 0
const subs = new Set<() => void>()
const bump = () => {
  version++
  subs.forEach((f) => f())
}
const subscribe = (f: () => void) => {
  subs.add(f)
  return () => subs.delete(f)
}
const snapshot = () => version

export const topModal = () => stack[stack.length - 1]
export const modalCount = () => stack.length
export const isOpen = (m: Modal) => stack.includes(m)
/** Force the host to re-read a modal whose title/body/foot were mutated in place. */
export const repaint = bump

export function open(m: Omit<Modal, 'id'>): Modal {
  const mm = { ...m, id: ++version }
  stack = [...stack, mm]
  bump()
  return mm
}

export function close(m: Modal) {
  stack = stack.filter((x) => x !== m)
  m.onClose?.()
  bump()
}

/** One yes/no. Resolves true only on y / Enter / the yes button. */
export function confirm(text: string, { yes = 'yes', no = 'no', title = 'gitdashy' } = {}): Promise<boolean> {
  return new Promise((res) => {
    const m = open({
      title,
      dismiss: false,
      body: () => <div>{text}</div>,
      foot: [
        ['y', yes, () => { close(m); res(true) }, 'go'],
        ['n', no, () => { close(m); res(false) }],
      ],
    })
    m.keys = {
      Enter: () => { close(m); res(true) },
      Escape: () => { close(m); res(false) },
    }
  })
}

/** A spinner for work with no other view. Opens after a beat so a fast call never flashes. */
export function busy<T>(title: string, text: string, run: () => Promise<T>): Promise<T> {
  let shown: Modal | null = null
  const timer = window.setTimeout(() => {
    shown = open({
      title,
      dismiss: false,
      body: () => (
        <div>
          <span className="spinner" /> {text}
        </div>
      ),
    })
  }, 250)
  return run().finally(() => {
    clearTimeout(timer)
    if (shown) close(shown)
  })
}

/** One message. Any dismiss closes it. */
export function notice(text: ReactNode, title = 'gitdashy'): Promise<void> {
  return new Promise((res) => {
    const m = open({
      title,
      body: () => <div>{text}</div>,
      foot: [['Enter', 'ok', () => { close(m); res() }]],
    })
    m.keys = { Escape: () => { close(m); res() } }
  })
}

/** A line of text. Resolves "" on cancel. */
export function prompt(text: string, value = '', title = 'gitdashy'): Promise<string> {
  return new Promise((res) => {
    const m = open({
      title,
      dismiss: false,
      focus: '#mi',
      body: () => (
        <>
          <div style={{ marginBottom: 10 }}>{text}</div>
          <input type="text" id="mi" defaultValue={value} />
        </>
      ),
      foot: [
        ['Enter', 'ok', () => { const v = (document.querySelector('#mi') as HTMLInputElement).value.trim(); close(m); res(v) }, 'go'],
        ['Esc', 'cancel', () => { close(m); res('') }],
      ],
    })
    m.keys = { Enter: () => m.foot![0][2](), Escape: () => m.foot![1][2]() }
  })
}

/** Read-only text, like less, under an optional `head`. Returns the modal so a caller can add a key of its own. */
export function viewer(title: string, text: string, sub = '', head?: ReactNode) {
  const m = open({
    title,
    sub,
    wide: !head,
    body: () => (
      <>
        {head}
        <pre>{text}</pre>
      </>
    ),
    foot: [['q', 'close', () => close(m)]],
  })
  m.keys = { Escape: () => close(m) }
  return m
}

/** A textarea and a save. */
export function editor(title: string, text: string, onSave: (v: string) => void | Promise<void>, sub = '') {
  const m = open({
    title,
    sub,
    wide: true,
    dismiss: false,
    focus: '#me',
    body: () => <textarea id="me" defaultValue={text} />,
    foot: [
      ['^S', 'save', async () => { await onSave((document.querySelector('#me') as HTMLTextAreaElement).value); close(m) }, 'go'],
      ['Esc', 'discard', () => close(m)],
    ],
  })
  m.keys = { Escape: () => close(m), 'ctrl+s': () => m.foot![0][2]() }
}

/** A list to pick from with j/k/Enter. `many` toggles a set of values instead of one. */
export function picker(title: string, options: string[], current: string, show: (v: string) => string, onPick: (v: string) => void): void
export function picker(title: string, options: string[], current: string[], show: (v: string) => string, onPick: (v: string[]) => void, many: true): void
export function picker(
  title: string,
  options: string[],
  current: string | string[],
  show: (v: string) => string,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onPick: (v: any) => void,
  many = false,
) {
  const chosen = () => (Array.isArray(current) ? current : [current])
  let idx = many ? 0 : Math.max(0, options.indexOf(current as string))
  const body = () =>
    options.map((o, i) => (
      <div key={o} className={`opt${i === idx ? ' on' : ''}`} onClick={() => pick(i)}>
        <span className="tick">{many ? (chosen().includes(o) ? '✓' : '') : o === current ? '✓' : ''}</span>
        <span>{show(o)}</span>
      </div>
    ))
  const m = open({
    title,
    body,
    foot: [['⏎', many ? 'toggle' : 'pick', () => pick(idx), 'go'], ['Esc', many ? 'close' : 'keep', () => close(m)]],
  })
  const pick = (i: number) => {
    idx = i
    if (!many) {
      close(m)
      onPick(options[i])
      return
    }
    const cur = chosen()
    current = cur.includes(options[i]) ? cur.filter((v) => v !== options[i]) : options.filter((v) => cur.includes(v) || v === options[i])
    onPick(current)
    bump()
  }
  m.keys = {
    j: () => { idx = (idx + 1) % options.length; bump() },
    k: () => { idx = (idx - 1 + options.length) % options.length; bump() },
    Enter: () => pick(idx),
    Escape: () => close(m),
  }
}

/** Type to fuzzy-find a login among `logins`, or follow one that is not there. Resolves "" on Esc. */
export function findLogin(title: string, logins: string[], following: string[]): Promise<string> {
  return new Promise((res) => {
    let q = ''
    let idx = 0
    const shown = () => {
      const hits = fuzzy(q, logins).slice(0, 50)
      const typed = q.trim()
      return isLogin(typed) && !hits.some((l) => l.toLowerCase() === typed.toLowerCase()) ? [...hits, typed] : hits
    }
    const done = (v: string) => {
      close(m)
      res(v)
    }
    const move = (by: number) => {
      const n = shown().length
      if (n) idx = (idx + by + n) % n
      bump()
    }
    const m = open({
      title,
      dismiss: false,
      focus: '#mi',
      body: () => (
        <>
          <input type="text" id="mi" placeholder="a GitHub username" autoComplete="off" onChange={(e) => { q = e.target.value; idx = 0; bump() }} />
          <div style={{ marginTop: 8 }}>
            {shown().map((l, i) => (
              <div key={l} className={`opt${i === idx ? ' on' : ''}`} onClick={() => done(l)}>
                <span className="tick">{following.some((f) => f.toLowerCase() === l.toLowerCase()) ? '✓' : ''}</span>
                <span>{logins.includes(l) ? l : `follow “${l}”`}</span>
              </div>
            ))}
            {logins.length || q ? null : <div style={{ color: 'var(--dim2)' }}>nobody on the board yet: type a username</div>}
          </div>
        </>
      ),
      foot: [
        ['Enter', 'follow', () => { const l = shown()[idx]; if (l) done(l) }, 'go'],
        ['Esc', 'cancel', () => done('')],
      ],
    })
    m.keys = { Enter: () => m.foot![0][2](), Escape: () => done(''), ArrowDown: () => move(1), ArrowUp: () => move(-1) }
  })
}

/** The stack. Mounted once, next to the app. */
export function ModalHost() {
  useSyncExternalStore(subscribe, snapshot)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const m = topModal()
      if (!m) return
      const key = e.ctrlKey && e.key === 's' ? 'ctrl+s' : e.key
      if (/input|textarea/i.test((e.target as HTMLElement).tagName) && !['Escape', 'Enter', 'ctrl+s', 'ArrowUp', 'ArrowDown'].includes(key)) return
      if (/textarea/i.test((e.target as HTMLElement).tagName) && key === 'Enter') return
      const foot = m.foot?.find(([k]) => k === key)
      const fn = foot ? foot[2] : m.keys?.[key]
      if (fn) {
        e.preventDefault()
        fn()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return (
    <>
      {stack.map((m) => (
        <div
          key={m.id}
          className="veil"
          onClick={(e) => {
            if (e.target === e.currentTarget && m.dismiss !== false) close(m)
          }}
        >
          <div className={`modal${m.wide ? ' wide' : ''}`}>
            <div className="mh">
              <b>{m.title}</b>
              {m.sub ? <em>{m.sub}</em> : null}
            </div>
            <div className="mb scroll">{m.body()}</div>
            {m.foot?.length ? (
              <div className="mf">
                {m.foot.map(([k, label, fn, cls]) => (
                  <span key={k} className={`btn ${cls || ''}`} onClick={fn}>
                    <kbd>{k}</kbd>
                    {label}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </>
  )
}