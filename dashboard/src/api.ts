// Same-origin client for the capture sidecar's JSON adapter. Auth = CAPTURE_KEY
// in localStorage['capture_key'] (shared with the capture flow), sent as X-Brain-Key.
const KEY_LS = 'capture_key'

export function getKey(): string {
  try { return localStorage.getItem(KEY_LS) || '' } catch { return '' }
}
export function setKey(k: string) {
  try { localStorage.setItem(KEY_LS, k.trim()) } catch { /* ignore */ }
}

async function req(path: string, opts: RequestInit = {}) {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>), 'X-Brain-Key': getKey() }
  const r = await fetch(path, { ...opts, headers })
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try { const j = await r.json(); detail = j.detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return r.json()
}

export interface Health { verdict: 'ok' | 'warn' | 'down'; services: Record<string, boolean>; feeds: { source: string; age_hours: number | null; stale: boolean }[] }
export interface Proposal { slug: string; title: string; status: string; action: string; target: string; rationale: string; rollback: string }
export interface Hit { score: number; slug: string; snippet: string }
export interface PageRow { slug: string; type: string; date: string; title: string }

export const api = {
  health: (): Promise<Health> => req('/api/health'),
  today: (): Promise<{ date: string | null; content: string }> => req('/api/today'),
  needs: (): Promise<{ proposals: Proposal[] }> => req('/api/needs'),
  pages: (source = '', n = 20): Promise<{ pages: PageRow[] }> => req(`/api/pages?n=${n}${source ? `&source=${source}` : ''}`),
  search: (q: string): Promise<{ hits: Hit[] }> => req(`/api/search?q=${encodeURIComponent(q)}&n=20`),
  page: (slug: string): Promise<{ slug: string; content: string }> => req(`/api/page?slug=${encodeURIComponent(slug)}`),
  decide: (slug: string, decision: 'approve' | 'reject') =>
    req('/api/decide', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slug, decision }) }),
  captureText: (text: string) =>
    req('/capture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, source: 'web' }) }),
  captureFile: (file: File | Blob, source: string, filename: string) => {
    const fd = new FormData()
    fd.append('source', source)
    fd.append('file', file, filename)
    return req('/capture', { method: 'POST', body: fd })
  },
}
