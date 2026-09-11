// The curses panel() as React dialogs. The vanilla kept a stack and painted innerHTML; here a tiny
// external store drives one ModalHost, and confirm/prompt/notice/viewer/editor/picker stay promise-based
// so the async actions that use them read the same as gui.html.
import type { ReactNode } from 'react'
import { useEffect, useSyncExternalStore } from 'react'

export type Foot = [key: string, label: string, fn: () => void, cls?: string]

type Modal = {
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

/** One message. Any dismiss closes it. */
export function notice(text: string, title = 'gitdashy'): Promise<void> {
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

/** Read-only text, like less. */
export function viewer(title: string, text: string, sub = '') {
  const m = open({
    title,
    sub,
    wide: true,
    body: () => <pre>{text}</pre>,
    foot: [['q', 'close', () => close(m)]],
  })
  m.keys = { Escape: () => close(m) }
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

/** A list to pick from with j/k/Enter. */
export function picker(title: string, options: string[], current: string, show: (v: string) => string, onPick: (v: string) => void) {
  let idx = Math.max(0, options.indexOf(current))
  const body = () =>
    options.map((o, i) => (
      <div key={o} className={`opt${i === idx ? ' on' : ''}`} onClick={() => pick(i)}>
        <span className="tick">{o === current ? '✓' : ''}</span>
        <span>{show(o)}</span>
      </div>
    ))
  const m = open({
    title,
    body,
    foot: [['⏎', 'pick', () => pick(idx), 'go'], ['Esc', 'keep', () => close(m)]],
  })
  const pick = (i: number) => {
    idx = i
    close(m)
    onPick(options[i])
  }
  m.keys = {
    j: () => { idx = (idx + 1) % options.length; bump() },
    k: () => { idx = (idx - 1 + options.length) % options.length; bump() },
    Enter: () => pick(idx),
    Escape: () => close(m),
  }
}

/** The stack. Mounted once, next to the app. */
export function ModalHost() {
  useSyncExternalStore(subscribe, snapshot)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const m = topModal()
      if (!m) return
      const key = e.ctrlKey && e.key === 's' ? 'ctrl+s' : e.key
      if (/input|textarea/i.test((e.target as HTMLElement).tagName) && !['Escape', 'Enter', 'ctrl+s'].includes(key)) return
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