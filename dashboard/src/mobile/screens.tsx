import { useRef, useState, type ReactNode } from 'react'
import { api, type Health, type Proposal } from '../api'
import { useAsync, type ViewOverride } from '../hooks'
import { Eyebrow, KpiBlock, Stadium, Fig, Card, Btn, Tag, Markdown, Loading, ErrorState, Empty } from '../kit/kit'
import { PageModal } from '../kit/PageModal'
import { Findings } from '../kit/Findings'
import type { ThemeMode } from '../theme'

function figs(text: string): ReactNode[] {
  return text.split(/(\$?\d[\d,.]*%?)/g).map((p, i) => (/^\$?\d[\d,.]*%?$/.test(p) ? <Fig key={i}>{p}</Fig> : <span key={i}>{p}</span>))
}
function highlights(md: string): string[] {
  return md.replace(/^---\n[\s\S]*?\n---\n?/, '').split('\n').map((l) => l.trim())
    .filter((l) => /^[-*]\s+/.test(l)).map((l) => l.replace(/^[-*]\s+/, '').replace(/\*\*/g, '').replace(/`/g, ''))
    .filter((l) => l.length > 8).slice(0, 3)
}

export function MobileToday({ navigate }: { navigate: (r: string) => void }) {
  const today = useAsync(() => api.today(), [])
  const stats = useAsync(() => api.stats(), [])
  const needs = useAsync(() => api.needs(), [])
  const pending = needs.data?.proposals.length ?? 0
  const now = new Date()
  const hl = today.data?.content ? highlights(today.data.content) : []
  return (
    <div className="mscreen view-fade">
      <Eyebrow>daily digest · {today.data?.date || now.toLocaleDateString()}</Eyebrow>
      <h1 className="display mtoday-date">{now.toLocaleDateString(undefined, { weekday: 'long' })}<br />{now.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</h1>

      <div className="mkpis">
        <KpiBlock label="pages" value={stats.data?.pages ?? 0} count />
        <KpiBlock label="needs" value={pending} count tone={pending ? 'warn' : 'live'} />
        <KpiBlock label="tags" value={stats.data?.tags ?? 0} count />
      </div>

      <Findings />

      {pending > 0 && (
        <button className="needcta press" onClick={() => navigate('needs')}>
          <span className="arr">→</span><span>{pending} proposal{pending > 1 ? 's' : ''} need you</span><span className="mono">open</span>
        </button>
      )}

      {hl.length > 0 && <div className="stadiums">{hl.map((h, i) => <Stadium key={i}>{figs(h)}</Stadium>)}</div>}

      <Card className="ink">
        <Eyebrow>digest</Eyebrow>
        {today.loading ? <Loading /> : today.err ? <ErrorState err={today.err} />
          : today.data?.content ? <div style={{ marginTop: 8 }}><Markdown src={today.data.content} /></div>
            : <Empty>No digest yet — the daily run writes one each morning.</Empty>}
      </Card>
    </div>
  )
}

export function MobileNeeds() {
  const { data, err, loading, reload } = useAsync(() => api.needs(), [])
  const [log, setLog] = useState<{ action: string; decision: string }[]>([])
  const [dx, setDx] = useState(0)
  const [busy, setBusy] = useState(false)
  const startX = useRef<number | null>(null)
  const props = data?.proposals || []
  const top = props[0]

  const decide = async (p: Proposal, d: 'approve' | 'reject') => {
    if (busy) return
    setBusy(true)
    try { await api.decide(p.slug, d); setLog((l) => [{ action: p.action || 'proposal', decision: d }, ...l].slice(0, 6)); setDx(0); reload() }
    catch { setDx(0) } finally { setBusy(false) }
  }
  const onDown = (e: React.PointerEvent) => { startX.current = e.clientX }
  const onMove = (e: React.PointerEvent) => { if (startX.current != null) setDx(e.clientX - startX.current) }
  const onUp = () => {
    if (startX.current == null) return
    if (top && dx > 120) decide(top, 'approve')
    else if (top && dx < -120) decide(top, 'reject')
    else setDx(0)
    startX.current = null
  }

  if (loading) return <div className="mscreen"><Loading /></div>
  if (err) return <div className="mscreen"><ErrorState err={err} /></div>

  return (
    <div className="mscreen view-fade">
      <div className="mhead"><Eyebrow>needs you</Eyebrow>{props.length > 0 && <Tag tone="warn">{props.length} pending</Tag>}</div>

      {!top ? <Empty>Nothing needs you right now. ✓</Empty> : (
        <div className="cardstack">
          {props.slice(0, 3).map((p, i) => {
            const isTop = i === 0
            const style = isTop
              ? { transform: `translateX(${dx}px) rotate(${dx / 28}deg)`, transition: startX.current == null ? 'transform .25s var(--bm-ease)' : 'none' }
              : { transform: `translateY(${i * 8}px) scale(${1 - i * 0.035})`, opacity: 1 - i * 0.18 }
            const glow = isTop ? (dx > 60 ? 'approve' : dx < -60 ? 'reject' : '') : ''
            return (
              <div key={p.slug} className={`swipecard ${isTop ? 'top' : ''} ${glow}`} style={{ ...style, zIndex: 10 - i }}
                onPointerDown={isTop ? onDown : undefined} onPointerMove={isTop ? onMove : undefined}
                onPointerUp={isTop ? onUp : undefined} onPointerCancel={isTop ? onUp : undefined}>
                <div className="swipetop"><Tag dot tone="live">agent</Tag><span className="mono footnote">{p.action}</span></div>
                <div className="display swipetitle">{p.target || p.title}</div>
                <p className="swipewhy">{p.rationale || p.title}</p>
                {p.rollback && <div className="footnote mono" style={{ marginTop: 'auto' }}>rollback · {p.rollback}</div>}
              </div>
            )
          })}
        </div>
      )}

      {top && (
        <div className="swipehints">
          <button className="press" onClick={() => decide(top, 'reject')} disabled={busy}>← reject</button>
          <span className="mono">swipe or tap</span>
          <button className="press approve" onClick={() => decide(top, 'approve')} disabled={busy}>approve →</button>
        </div>
      )}

      {log.length > 0 && (
        <div className="declog">
          <Eyebrow>this session</Eyebrow>
          {log.map((l, i) => (
            <div key={i} className="declog-row"><span className={`mono ${l.decision === 'approve' ? 'ok' : 'mut'}`}>{l.decision === 'approve' ? 'APPROVED' : 'REJECTED'}</span><span>{l.action}</span></div>
          ))}
        </div>
      )}
    </div>
  )
}

export function MobileSearch() {
  const [q, setQ] = useState('')
  const [ran, setRan] = useState('')
  const { data, err, loading } = useAsync(() => (ran.trim().length > 1 ? api.search(ran, 25) : Promise.resolve({ hits: [] })), [ran])
  const [open, setOpen] = useState('')
  return (
    <div className="mscreen view-fade">
      <Eyebrow>search</Eyebrow>
      <input className="field mfield" placeholder="Search the brain…" value={q} autoFocus
        onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') setRan(q) }} />
      {err && <ErrorState err={err} />}
      {loading && ran ? <Loading /> : (
        <div className="mresults">
          {(data?.hits || []).map((h) => (
            <button key={h.slug} className="mresult press" onClick={() => setOpen(h.slug)}>
              <div className="mresult-top"><span className="score">{h.score.toFixed(2)}</span><span className="mono mresult-src">{h.slug.split('/')[0]}</span></div>
              <div className="mresult-txt">{h.snippet.replace(/^[-#\s]+/, '').slice(0, 120) || h.slug}</div>
            </button>
          ))}
          {ran && !loading && (data?.hits.length ?? 0) === 0 && <Empty>No hits for “{ran}”.</Empty>}
        </div>
      )}
      {open && <PageModal slug={open} onClose={() => setOpen('')} />}
    </div>
  )
}

export function MobileCapture() {
  const [text, setText] = useState('')
  const [status, setStatus] = useState<{ msg: string; cls: string }>({ msg: 'Ready.', cls: '' })
  const [rec, setRec] = useState(false)
  const mr = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const ok = (m: string) => setStatus({ msg: m, cls: 'ok' })
  const bad = (m: string) => setStatus({ msg: m, cls: 'bad' })

  const sendText = async () => {
    const v = text.trim(); if (!v) return bad('Nothing to send.')
    setStatus({ msg: 'Sending…', cls: '' })
    try { const r = await api.captureText(v); ok(r.duplicate ? 'Duplicate — skipped.' : 'Captured ✓'); if (!r.duplicate) setText('') }
    catch (e) { bad(String((e as Error).message)) }
  }
  const sendFile = async (file: File | Blob, source: string, name: string, working: string) => {
    setStatus({ msg: working, cls: '' })
    try { const r = await api.captureFile(file, source, name); ok(r.duplicate ? 'Duplicate — skipped.' : `Captured ✓ (${r.via})`) }
    catch (e) { bad(String((e as Error).message)) }
  }
  const toggleRec = async () => {
    if (mr.current?.state === 'recording') { mr.current.stop(); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = new MediaRecorder(stream); chunks.current = []
      r.ondataavailable = (e) => chunks.current.push(e.data)
      r.onstop = () => { setRec(false); const b = new Blob(chunks.current, { type: r.mimeType || 'audio/mp4' }); const ext = b.type.includes('webm') ? 'webm' : 'm4a'; sendFile(b, 'voice', 'voice.' + ext, 'Transcribing…'); stream.getTracks().forEach((t) => t.stop()) }
      r.start(); mr.current = r; setRec(true); setStatus({ msg: 'Recording… tap Stop.', cls: '' })
    } catch (e) { bad('Mic blocked: ' + String((e as Error).message)) }
  }
  return (
    <div className="mscreen view-fade">
      <Eyebrow>capture</Eyebrow>
      <textarea className="cap mcap" placeholder="A thought, a ticker, a link…" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="mcaprow">
        <Btn kind="primary" onClick={sendText}>send</Btn>
        <button className={`btn press ${rec ? 'recon' : 'ghost'}`} onClick={toggleRec}>{rec ? '● stop' : '🎤 voice'}</button>
        <label className="btn ghost press mphoto">📷 photo
          <input type="file" accept="image/*" onChange={(e) => { const f = e.target.files?.[0]; if (f) sendFile(f, 'image', f.name, 'Reading…') }} />
        </label>
      </div>
      <div className={`status ${status.cls}`}>{status.msg}</div>
    </div>
  )
}

export function MobileMore({ health, mode, cycleTheme, onKey, override, setOverride }: {
  health: Health | null; mode: ThemeMode; cycleTheme: () => void; onKey: () => void
  override: ViewOverride; setOverride: (v: ViewOverride) => void
}) {
  const v = health?.verdict
  const row = (label: ReactNode, meta: ReactNode, onClick?: () => void, accent?: boolean) => (
    <button className="morerow press" onClick={onClick} disabled={!onClick}>
      <span className="arr">→</span><span className="morelabel">{label}</span>
      <span className={`mono moremeta ${accent ? 'ok' : ''}`}>{meta}</span>
    </button>
  )
  return (
    <div className="mscreen view-fade">
      <Eyebrow>settings · self-host</Eyebrow>
      <h2 className="display mmore-h">your machine.<br />your keys.</h2>
      <div className="morelist">
        {row('api key', 'paste', onKey)}
        {row('system', v || '—', undefined, v === 'ok')}
        {row('theme', mode.toLowerCase(), cycleTheme)}
        {row('layout', override, () => setOverride(override === 'auto' ? 'desktop' : override === 'desktop' ? 'mobile' : 'auto'))}
      </div>
      <Eyebrow style={{ marginTop: 22 }}>services</Eyebrow>
      <div className="morelist">
        {health?.services && Object.entries(health.services).map(([k, up]) => (
          <div key={k} className="morerow"><span className="arr">→</span><span className="morelabel">{k}</span><Tag tone={up ? 'live' : 'err'} dot>{up ? 'up' : 'down'}</Tag></div>
        ))}
      </div>
      <Eyebrow style={{ marginTop: 22 }}>feeds</Eyebrow>
      <div className="morelist">
        {health?.feeds.map((f) => (
          <div key={f.source} className="morerow"><span className="arr">→</span><span className="morelabel">{f.source}</span><span className={`mono moremeta ${f.stale ? '' : 'ok'}`}>{f.age_hours != null ? `${f.age_hours}h` : '—'}</span></div>
        ))}
      </div>
      <p className="footnote" style={{ marginTop: 20 }}>Add to Home Screen for a full-screen app.</p>
    </div>
  )
}
