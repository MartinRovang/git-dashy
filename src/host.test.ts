import { afterEach, describe, expect, it } from 'vitest'
import { hostApps } from './host'

const w = globalThis as { __ICECREAM__?: unknown }
const g = { id: 'gitdashy', name: 'gitdashy', icon: 'data:image/png;base64,GG', href: 'icecream://switch/gitdashy', key: 'Ctrl+1', current: true }
const n = { id: 'neodeploy', name: 'neodeploy', icon: 'data:image/png;base64,NN', href: 'icecream://switch/neodeploy', key: 'Ctrl+2', current: false }

describe('hostApps', () => {
  afterEach(() => {
    delete w.__ICECREAM__
  })

  it('is null standalone', () => {
    expect(hostApps()).toBeNull()
  })

  it('reads the apps icecream hands over', () => {
    w.__ICECREAM__ = { apps: [g, n] }
    expect(hostApps()).toEqual([g, n])
  })

  it('drops an app that is not a switch link with an inline icon', () => {
    for (const bad of [{ ...n, href: 'javascript:alert(1)' }, { ...n, href: 'https://evil.example' }, { ...n, icon: 'https://evil.example/i.png' }, { ...n, name: 7 }]) {
      w.__ICECREAM__ = { apps: [g, bad] }
      expect(hostApps()).toEqual([g])
    }
  })

  it('is null on nonsense', () => {
    for (const v of ['nonsense', { apps: 'x' }, { apps: [] }, { switch: {} }]) {
      w.__ICECREAM__ = v
      expect(hostApps()).toBeNull()
    }
  })
})
