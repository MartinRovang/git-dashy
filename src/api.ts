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

export async function errorText(r: Response): Promise<string> {
  try {
    return (await r.json()).error || r.statusText
  } catch {
    return r.statusText || 'server gone'
  }
}
