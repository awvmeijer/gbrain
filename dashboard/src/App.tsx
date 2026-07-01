import { useEffect, useRef, useState } from 'react'
import { useTheme } from './theme'
import { api, getKey, setKey, type Health, type Proposal, type Hit } from './api'
import { renderMarkdown } from './md'

type Tab = 'today' | 'needs' | 'search' | 'capture'

export default function App() {
  const { resolved, mode, setMode } = useTheme()
  const [tab, setTab] = useState<Tab>('today')
  const [health, setHealth] = useState<Health | null>(null)
  const [key, setKeyState] = useState(getKey())
  const [showGate, setShowGate] = useState(!getKey())

  // Dev-only convenience: seed the key from VITE_DEV_KEY (sourced from Keychain
  // by `npm run dev`) so previews show real data. Stripped from prod builds.
  useEffect(() => {
    const env = (import.meta as unknown as { env: Record<string, string> }).env
    if (env?.DEV && env?.VITE_DEV_KEY && !getKey()) {
      setKey(env.VITE_DEV_KEY); setKeyState(env.VITE_DEV_KEY); setShowGate(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!key) return
    let live = true
    const load = () => api.health().then((h) => live && setHealth(h)).catch(() => {})
    load()
    const t = setInterval(load, 60000)
    return () => { live = false; clearInterval(t) }
  }, [key])

  const saveKey = (k: string) => { setKey(k); setKeyState(k); if (k) setShowGate(false) }
  const cycleMode = () => setMode(mode === 'AUTO' ? 'DAY' : mode === 'DAY' ? 'NIGHT' : 'AUTO')
  const v = health?.verdict

  return (
    <div className="brains-root" data-th={resolved}>
      <div className="shell">
        <header className="hdr">
          <span className="wordmark">BRAINS<span className="slash">/</span></span>
          <span className="spacer" />
          {v && <span className={`verdict ${v}`}><span className="dot" />{v}</span>}
          <button className="themebtn" onClick={cycleMode}>{mode}</button>
          <button className="themebtn" onClick={() => setShowGate((s) => !s)}>key</button>
        </header>

        {showGate && (
          <div className="content" style={{ paddingBottom: 0 }}>
            <div className="card gate">
              <input className="field" type="password" placeholder="paste CAPTURE_KEY" defaultValue={key}
                onKeyDown={(e) => e.key === 'Enter' && saveKey((e.target as HTMLInputElement).value)} />
              <button className="btn approve" onClick={(e) => {
                const inp = (e.currentTarget.previousElementSibling as HTMLInputElement)
                saveKey(inp.value)
              }}>save</button>
            </div>
          </div>
        )}

        <nav className="tabs">
          {(['today', 'needs', 'search', 'capture'] as Tab[]).map((t) => (
            <button key={t} className={`tab ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}>
              {t === 'today' ? 'Today' : t === 'needs' ? 'Needs you' : t === 'search' ? 'Search' : 'Capture'}
              {t === 'needs' && health && <span className="n" />}
            </button>
          ))}
        </nav>

        <main className="content">
          {!key ? <p className="muted">Set your CAPTURE_KEY (tap “key”) to begin.</p>
            : tab === 'today' ? <Today />
              : tab === 'needs' ? <Needs />
                : tab === 'search' ? <Search />
                  : <Capture />}
        </main>
      </div>
    </div>
  )
}

function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const reload = () => {
    setLoading(true); setErr('')
    fn().then(setData).catch((e) => setErr(String(e.message || e))).finally(() => setLoading(false))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, deps)
  return { data, err, loading, reload }
}

function Today() {
  const { data, err, loading } = useAsync(() => api.today())
  if (loading) return <p className="muted">Loading…</p>
  if (err) return <p className="err">{err}</p>
  if (!data?.content) return <p className="muted">No digest yet — the daily run writes one each morning.</p>
  return (
    <div className="card">
      <div className="eyebrow"><span className="sq" />DAILY DIGEST · {data.date}</div>
      <div className="md" style={{ marginTop: 10 }} dangerouslySetInnerHTML={{ __html: renderMarkdown(data.content) }} />
    </div>
  )
}

function Needs() {
  const { data, err, loading, reload } = useAsync(() => api.needs())
  const [busy, setBusy] = useState('')
  const decide = async (p: Proposal, d: 'approve' | 'reject') => {
    setBusy(p.slug)
    try { await api.decide(p.slug, d); reload() } finally { setBusy('') }
  }
  if (loading) return <p className="muted">Loading…</p>
  if (err) return <p className="err">{err}</p>
  const props = data?.proposals || []
  if (!props.length) return <p className="muted">Nothing needs you right now. ✓</p>
  return (
    <>
      {props.map((p: Proposal) => (
        <div key={p.slug} className="card prop">
          <div className="act">{p.action || 'proposal'} <span className="target">→ {p.target}</span></div>
          <div className="why">{p.rationale || p.title}</div>
          {p.rollback && <div className="muted">Rollback: {p.rollback}</div>}
          <div className="btns">
            <button className="btn approve" disabled={!!busy} onClick={() => decide(p, 'approve')}>Approve</button>
            <button className="btn reject" disabled={!!busy} onClick={() => decide(p, 'reject')}>Reject</button>
          </div>
        </div>
      ))}
    </>
  )
}

function Search() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[] | null>(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<{ slug: string; content: string } | null>(null)
  const run = async () => {
    if (!q.trim()) return
    setErr(''); setHits(null); setOpen(null)
    try { setHits((await api.search(q)).hits) } catch (e) { setErr(String((e as Error).message)) }
  }
  const openPage = async (slug: string) => {
    try { setOpen(await api.page(slug)) } catch (e) { setErr(String((e as Error).message)) }
  }
  return (
    <>
      <input className="field" placeholder="Search the brain…" value={q}
        onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
      {err && <p className="err">{err}</p>}
      {open ? (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="eyebrow"><span className="sq" />{open.slug}</div>
          <button className="themebtn" style={{ float: 'right' }} onClick={() => setOpen(null)}>close</button>
          <div className="md" style={{ marginTop: 10 }} dangerouslySetInnerHTML={{ __html: renderMarkdown(open.content) }} />
        </div>
      ) : hits && (
        <div className="card" style={{ marginTop: 12 }}>
          {hits.length === 0 && <p className="muted">No hits.</p>}
          {hits.map((h) => (
            <a key={h.slug} className="rowlink" onClick={() => openPage(h.slug)}>
              <span className="score">{h.score.toFixed(2)}</span>{'  '}{h.snippet.replace(/^-+$/, '(…)').slice(0, 90) || h.slug}
              <div className="slug">{h.slug}</div>
            </a>
          ))}
        </div>
      )}
    </>
  )
}

function Capture() {
  const [text, setText] = useState('')
  const [status, setStatus] = useState<{ msg: string; cls: string }>({ msg: 'Ready.', cls: '' })
  const [recording, setRecording] = useState(false)
  const mr = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])

  const ok = (m: string) => setStatus({ msg: m, cls: 'ok' })
  const bad = (m: string) => setStatus({ msg: m, cls: 'bad' })

  const sendText = async () => {
    const v = text.trim()
    if (!v) return bad('Nothing to send.')
    setStatus({ msg: 'Sending…', cls: '' })
    try {
      const r = await api.captureText(v)
      ok(r.duplicate ? 'Duplicate — skipped.' : 'Captured ✓\n' + (r.text || '').slice(0, 200))
      if (!r.duplicate) setText('')
    } catch (e) { bad(String((e as Error).message)) }
  }

  const sendFile = async (file: File | Blob, source: string, name: string, working: string) => {
    setStatus({ msg: working, cls: '' })
    try {
      const r = await api.captureFile(file, source, name)
      ok(r.duplicate ? 'Duplicate — skipped.' : 'Captured ✓ (' + r.via + ')\n' + (r.text || '').slice(0, 220))
    } catch (e) { bad(String((e as Error).message)) }
  }

  const toggleRec = async () => {
    if (mr.current && mr.current.state === 'recording') { mr.current.stop(); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunks.current = []
      rec.ondataavailable = (e) => chunks.current.push(e.data)
      rec.onstop = () => {
        setRecording(false)
        const blob = new Blob(chunks.current, { type: rec.mimeType || 'audio/mp4' })
        const ext = blob.type.includes('webm') ? 'webm' : 'm4a'
        sendFile(blob, 'voice', 'voice.' + ext, 'Transcribing…')
        stream.getTracks().forEach((t) => t.stop())
      }
      rec.start(); mr.current = rec; setRecording(true); setStatus({ msg: 'Recording… tap Stop.', cls: '' })
    } catch (e) { bad('Mic blocked: ' + String((e as Error).message)) }
  }

  return (
    <>
      <textarea className="cap" placeholder="Type or paste anything — a thought, a ticker, a link…"
        value={text} onChange={(e) => setText(e.target.value)} />
      <div className="caprow">
        <button className="btn approve" onClick={sendText}>Send</button>
        <button className={`btn rec ${recording ? 'on' : ''}`} onClick={toggleRec}>{recording ? '● Stop' : '🎤 Record'}</button>
      </div>
      <div className="caprow">
        <button className="btn photo">📷 Photo / Screenshot
          <input type="file" accept="image/*"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) sendFile(f, 'image', f.name, 'Reading…') }} />
        </button>
      </div>
      <div className={`status ${status.cls}`}>{status.msg}</div>
    </>
  )
}
