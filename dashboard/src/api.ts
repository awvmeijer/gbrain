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
    const e = new Error(detail) as Error & { status?: number }
    e.status = r.status
    throw e
  }
  return r.json()
}

export interface Health { verdict: 'ok' | 'warn' | 'down'; services: Record<string, boolean>; feeds: Feed[] }
export interface Feed { source: string; age_hours: number | null; stale: boolean }
export interface Proposal { slug: string; title: string; status: string; action: string; target: string; rationale: string; rollback: string }
export interface Hit { score: number; slug: string; snippet: string }
export interface PageRow { slug: string; type: string; date: string; title: string }
export interface Stats { pages: number; chunks: number; embedded: number; links: number; tags: number; timeline: number; by_type: Record<string, number> }
export interface OpsDetail { score: number | null; score_max: number | null; embed_coverage: number | null; missing_embeddings: number; stale_pages: number; orphan_pages: number }
export interface OpsData { health: Health; stats: Stats; detail: OpsDetail; jobs: { id: string; status: string; raw: string }[] }
export interface GraphNode { slug: string; source: string; type: string; title: string; hub?: boolean; deg?: number }
export interface GraphEdge { from: string; to: string; type: string }
export interface GraphData { mode: 'corpus' | 'relations'; nodes: GraphNode[]; edges: GraphEdge[]; sources?: { source: string; count: number }[]; seed?: string; seeds?: string[]; auto_seed?: string }
export interface Finding { slug: string; ticker: string; score: number; window: string; classes: string[]; title: string }

type Sort = 'updated_desc' | 'created_desc' | 'slug'

export const api = {
  health: (): Promise<Health> => req('/api/health'),
  today: (): Promise<{ date: string | null; content: string }> => req('/api/today'),
  needs: (): Promise<{ proposals: Proposal[] }> => req('/api/needs'),
  stats: (): Promise<Stats> => req('/api/stats'),
  ops: (): Promise<OpsData> => req('/api/ops'),
  graph: (slug = ''): Promise<GraphData> => req(`/api/graph${slug ? `?slug=${encodeURIComponent(slug)}` : ''}`),
  findings: (): Promise<{ findings: Finding[] }> => req('/api/findings'),
  pages: (source = '', n = 30, sort: Sort = 'updated_desc'): Promise<{ pages: PageRow[] }> =>
    req(`/api/pages?n=${n}&sort=${sort}${source ? `&source=${encodeURIComponent(source)}` : ''}`),
  search: (q: string, n = 20): Promise<{ hits: Hit[] }> => req(`/api/search?q=${encodeURIComponent(q)}&n=${n}`),
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

// ---- Streaming ask (the AI-agent surface). EventSource can't POST, so we read
// the SSE stream off fetch's ReadableStream. think (synthesis) by default; fast
// → query (hybrid search, no LLM). ----
export interface AskHandlers {
  onStart?: (mode: string) => void
  onToken?: (text: string) => void
  onDone?: (grounding: string[]) => void
  onError?: (detail: string) => void
}

export async function ask(q: string, opts: { fast?: boolean } = {}, h: AskHandlers = {}, signal?: AbortSignal) {
  let r: Response
  try {
    r = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'X-Brain-Key': getKey(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ q, fast: !!opts.fast }),
      signal,
    })
  } catch (e) {
    h.onError?.(String((e as Error).message)); return
  }
  if (!r.ok || !r.body) { h.onError?.(`HTTP ${r.status}`); return }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    let chunk: ReadableStreamReadResult<Uint8Array>
    try { chunk = await reader.read() } catch { break }
    if (chunk.done) break
    buf += dec.decode(chunk.value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const p of parts) {
      const line = p.trim()
      if (!line.startsWith('data:')) continue
      try {
        const ev = JSON.parse(line.slice(5).trim())
        if (ev.event === 'start') h.onStart?.(ev.mode)
        else if (ev.event === 'token') h.onToken?.(ev.text)
        else if (ev.event === 'done') h.onDone?.(ev.grounding || [])
        else if (ev.event === 'error') h.onError?.(ev.detail || 'error')
      } catch { /* ignore malformed frame */ }
    }
  }
}
