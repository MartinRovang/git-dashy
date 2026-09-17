import type { ReactNode } from 'react'

/** One settings line: optional key hint, label, and the control (a select or chips). */
export function Row({ k, label, children }: { k?: string; label: string; children: ReactNode }) {
  return (
    <div className="row">
      <span>
        {k ? <kbd className="hint">{k}</kbd> : null}
        {label}
      </span>
      {children}
    </div>
  )
}

/** The platform <select>, as the vanilla pick() built — same "current value first if missing" rule. */
export function Select({
  value,
  options,
  show,
  onChange,
}: {
  value: string
  options: string[]
  show: (v: string) => string
  onChange: (v: string) => void
}) {
  const all = options.includes(value) ? options : [value, ...options]
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {all.map((o) => (
        <option key={o} value={o}>
          {show(o)}
        </option>
      ))}
    </select>
  )
}
