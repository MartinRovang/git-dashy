import { afterEach, describe, expect, it } from 'vitest'
import { hostSwitch } from './host'

const w = globalThis as { __NEO_SUITE__?: unknown }

describe('hostSwitch', () => {
  afterEach(() => {
    delete w.__NEO_SUITE__
  })

  it('is null standalone', () => {
    expect(hostSwitch()).toBeNull()
  })

  it('reads the button neo-suite hands over', () => {
    w.__NEO_SUITE__ = { switch: { href: 'neo-suite://switch/neodeploy', icon: 'data:image/png;base64,AA', title: 'neodeploy · Ctrl+2' } }
    expect(hostSwitch()).toEqual({ href: 'neo-suite://switch/neodeploy', icon: 'data:image/png;base64,AA', title: 'neodeploy · Ctrl+2' })
  })

  it('refuses anything but a neo-suite switch link', () => {
    for (const href of ['javascript:alert(1)', 'https://evil.example', '', 7]) {
      w.__NEO_SUITE__ = { switch: { href, icon: 'data:image/png;base64,AA', title: 't' } }
      expect(hostSwitch()).toBeNull()
    }
    w.__NEO_SUITE__ = { switch: { href: 'neo-suite://switch/x', icon: 'https://evil.example/i.png', title: 't' } }
    expect(hostSwitch()).toBeNull()
    w.__NEO_SUITE__ = 'nonsense'
    expect(hostSwitch()).toBeNull()
  })
})
