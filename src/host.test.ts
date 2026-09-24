import { afterEach, describe, expect, it } from 'vitest'
import { hostSwitch } from './host'

const w = globalThis as { __ICECREAM__?: unknown }

describe('hostSwitch', () => {
  afterEach(() => {
    delete w.__ICECREAM__
  })

  it('is null standalone', () => {
    expect(hostSwitch()).toBeNull()
  })

  it('reads the button icecream hands over', () => {
    w.__ICECREAM__ = { switch: { href: 'icecream://switch/neodeploy', icon: 'data:image/png;base64,AA', title: 'neodeploy · Ctrl+2' } }
    expect(hostSwitch()).toEqual({ href: 'icecream://switch/neodeploy', icon: 'data:image/png;base64,AA', title: 'neodeploy · Ctrl+2' })
  })

  it('refuses anything but a icecream switch link', () => {
    for (const href of ['javascript:alert(1)', 'https://evil.example', '', 7]) {
      w.__ICECREAM__ = { switch: { href, icon: 'data:image/png;base64,AA', title: 't' } }
      expect(hostSwitch()).toBeNull()
    }
    w.__ICECREAM__ = { switch: { href: 'icecream://switch/x', icon: 'https://evil.example/i.png', title: 't' } }
    expect(hostSwitch()).toBeNull()
    w.__ICECREAM__ = 'nonsense'
    expect(hostSwitch()).toBeNull()
  })
})
