import { useEffect, useRef, useState } from 'react'
import { ask, api } from '../api'
import { DotMark, Eyebrow, Tag, Markdown, Btn } from '../kit/kit'

// The persistent AI-agent presence. Controlled by App so ⌘K / the FAB can open
// it. Streams /api/ask (think → synthesis, fast → query), the DotMark pulses
// while it works, and cited pages surface as grounding chips.
export function AgentDock({ open, setOpen, mobile, seedQ }: {
  open: boolean; setOpen: (v: boolean) => void; mobile: boolean; seedQ?: string
}) {
  const [q, setQ] = useState('')
  const [fast, setFast] = useState(false)
  const [answer, setAnswer] = useState('')
  const [mode, setMode] = useState<string>('')
  const [streaming, setStreaming] = useState(false)
  const [grounding, setGrounding] = useState<string[]>([])
  const [err, setErr] = useState('')
  const [page, setPage] = useState<{ slug: string; content: string } | null>(null)
  const abort = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => { if (open) { setTimeout(() => inputRef.current?.focus(), 50); if (seedQ) setQ(seedQ) } }, [open, seedQ])
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight }, [answer])
  useEffect(() => () => abort.current?.abort(), [])

  const run = () => {
    const question = q.trim()
    if (!question || streaming) return
    abort.current?.abort()
    const ac = new AbortController(); abort.current = ac
    setAnswer(''); setGrounding([]); setErr(''); setMode(''); setStreaming(true); setPage(null)
    ask(question, { fast }, {
      onStart: (m) => setMode(m),
      onToken: (t) => setAnswer((a) => a + t),
      onDone: (g) => { setGrounding(g); setStreaming(false) },
      onError: (d) => { setErr(d); setStreaming(false) },
    }, ac.signal)
  }
  const stop = () => { abort.current?.abort(); setStreaming(false) }
  const openSlug = async (slug: string) => {
    try { setPage(await api.page(slug)) } catch (e) { setErr(String((e as Error).message)) }
  }

  const panel = (
    <div className="agentpanel" role="dialog" aria-label="Ask the brain">
      <div className="agenthead">
        <DotMark size={22} thinking={streaming} />
        <Eyebrow>ask the brain{mode && <Tag>{mode}</Tag>}</Eyebrow>
        <span style={{ flex: 1 }} />
        <button className="agentx" onClick={() => setOpen(false)} aria-label="Close">✕</button>
      </div>

      <div className="agentbody" ref={bodyRef}>
        {!answer && !err && !streaming && (
          <p className="muted agenthint">Ask a question across everything the brain has read. Synthesis is cited — sources appear below the answer.</p>
        )}
        {err && <p className="err">{/401/.test(err) ? '401 — set your API key (tap “key”).' : err}</p>}
        {page ? (
          <div className="agentpage">
            <div className="agenthead" style={{ padding: 0, border: 'none' }}>
              <Eyebrow>{page.slug}</Eyebrow><span style={{ flex: 1 }} />
              <button className="agentx" onClick={() => setPage(null)}>back</button>
            </div>
            <Markdown src={page.content} />
          </div>
        ) : answer ? (
          streaming
            ? <div className="ask-answer">{answer}<span className="caret">▍</span></div>
            : <div className="ask-answer"><Markdown src={answer} /></div>
        ) : streaming ? <p className="muted">thinking<span className="caret">▍</span></p> : null}

        {!page && grounding.length > 0 && (
          <div className="grounding">
            <Eyebrow>grounded in</Eyebrow>
            <div className="chips">
              {grounding.map((s) => <button key={s} className="chip press" onClick={() => openSlug(s)}>{s}</button>)}
            </div>
          </div>
        )}
      </div>

      <div className="agentinput">
        <input ref={inputRef} className="field" placeholder="Ask across the brain…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') run(); if (e.key === 'Escape') setOpen(false) }} />
        <button className={`fasttoggle press ${fast ? 'on' : ''}`} title="Fast (search only, no synthesis)" onClick={() => setFast((f) => !f)}>fast</button>
        {streaming ? <Btn kind="ghost" onClick={stop}>stop</Btn> : <Btn kind="primary" onClick={run}>ask</Btn>}
      </div>
    </div>
  )

  if (mobile) {
    return (
      <>
        <button className={`agentfab press ${open ? 'hide' : ''}`} onClick={() => setOpen(true)} aria-label="Ask the brain">
          <DotMark size={30} thinking={streaming} />
        </button>
        {open && <div className="agentsheet">{panel}</div>}
      </>
    )
  }
  return (
    <>
      {!open && (
        <button className="agentdockbar press" onClick={() => setOpen(true)}>
          <DotMark size={22} thinking={streaming} />
          <span className="mono">ask the brain</span>
          <span className="kbd">⌘K</span>
        </button>
      )}
      {open && <div className="agentdock">{panel}</div>}
    </>
  )
}
