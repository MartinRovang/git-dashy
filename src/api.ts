// The HTTP surface of the in-process Rust server (src-tauri/src/web.rs). The page is served with
// ?token=…, which every call must repeat in X-Dashy-Token.
export const TOKEN = new URLSearchParams(location.search).get('token') || ''

export const api = (path: string, init?: RequestInit) =>
  fetch(path, { ...init, headers: { 'X-Dashy-Token': TOKEN, ...(init?.headers || {}) } })

export const post = (path: string, body?: unknown) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })

/** Put `text` on the clipboard and return the line to flash. The page's own clipboard first: the server's
 *  /api/copy needs wl-copy, xclip or xsel on PATH, and without one it can only write an OSC 52 escape to a
 *  terminal the app window does not have, so the copy silently went nowhere. */
export async function copyText(text: string, what: string): Promise<string> {
  try {
    await navigator.clipboard.writeText(text)
    return `✓ copied ${what}`
  } catch {
    /* no clipboard API in this page, or it was refused: ask the server */
  }
  const r = await post('/api/copy', { text })
  if (!r.ok) return `✗ ${await errorText(r)}`
  const { tool } = await r.json()
  return tool === 'terminal' ? `✗ could not copy ${what}: install wl-clipboard or xclip` : `✓ copied ${what} (via ${tool})`
}

export async function errorText(r: Response): Promise<string> {
  try {
    return (await r.json()).error || r.statusText
  } catch {
    return r.statusText || 'server gone'
  }
}
