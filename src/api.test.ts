import { afterEach, describe, expect, it, vi } from 'vitest'

// api.ts reads the token off location when it loads, and vitest runs in node
vi.stubGlobal('location', { search: '?token=t' })
const { copyText } = await import('./api')

const served = (tool: string) => vi.fn(async () => new Response(JSON.stringify({ ok: true, tool })))

describe('copyText', () => {
  afterEach(() => vi.unstubAllGlobals())

  it("uses the page's clipboard and never asks the server when it works", async () => {
    const writeText = vi.fn(async () => {})
    const fetch = served('xclip')
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    vi.stubGlobal('fetch', fetch)
    expect(await copyText('body', 'the pre-review')).toBe('✓ copied the pre-review')
    expect(writeText).toHaveBeenCalledWith('body')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('falls back to the server, and says so when it had no clipboard tool', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: async () => Promise.reject(new Error('denied')) } })
    vi.stubGlobal('fetch', served('xclip'))
    expect(await copyText('body', 'the pre-review')).toBe('✓ copied the pre-review (via xclip)')
    vi.stubGlobal('navigator', {})
    vi.stubGlobal('fetch', served('terminal'))
    expect(await copyText('body', 'the pre-review')).toBe('✗ could not copy the pre-review: install wl-clipboard or xclip')
  })
})
