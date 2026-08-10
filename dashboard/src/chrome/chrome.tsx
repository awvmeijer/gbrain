import { useEffect, useRef, useState, type ReactNode } from 'react'
import { DotMark, Verdict, Eyebrow, Btn } from '../kit/kit'
import type { Health } from '../api'
import type { ThemeMode } from '../theme'

export const DESKTOP_SECTIONS = [
  { key: 'today', label: 'Today' },
  { key: 'graph', label: 'Graph' },
  { key: 'records', label: 'Records' },
  { key: 'ops', label: 'Ops' },
]
export const MOBILE_TABS = [
  { key: 'today', label: 'Today' },
  { key: 'needs', label: 'Needs' },
  { key: 'search', label: 'Search' },
  { key: 'capture', label: 'Capture' },
  { key: 'more', label: 'More' },
]

function Wordmark() {
  return (
    <span className="brandlock">
      <DotMark size={22} />
      <span className="wordmark brand">BRAINS<span className="slash">/</span></span>
    </span>
  )
}

export function TopRail({ route, navigate, health, mode, cycleTheme, onKey, onPalette }: {
  route: string; navigate: (r: string) => void; health: Health | null
  mode: ThemeMode; cycleTheme: () => void; onKey: () => void; onPalette: () => void
}) {
  const active = route.split('/')[0]
  return (
    <header className="toprail">
      <div className="toprail-l">
        <Wordmark />
        <nav className="railnav">
          {DESKTOP_SECTIONS.map((s) => (
            <button key={s.key} className={`railtab ${active === s.key ? 'on' : ''}`} onClick={() => navigate(s.key)}>{s.label}</button>
          ))}
        </nav>
      </div>
      <div className="toprail-r">
        <Verdict health={health} />
        <button className="chipbtn press" onClick={onPalette} title="Command palette">⌘K</button>
        <button className="chipbtn press" onClick={cycleTheme} title="Theme">{mode}</button>
        <button className="chipbtn press" onClick={onKey} title="API key">key</button>
      </div>
    </header>
  )
}

export function HudRibbon({ items }: { items: [string, ReactNode][] }) {
  return (
    <footer className="hud mono">
      {items.map(([k, v], i) => <span key={i} className="hud-item">{k} <b>{v}</b></span>)}
    </footer>
  )
}

export function BottomTabs({ route, navigate, needsCount }: { route: string; navigate: (r: string) => void; needsCount?: number }) {
  const active = route.split('/')[0]
  return (
    <nav className="bottomtabs">
      {MOBILE_TABS.map((t) => (
        <button key={t.key} className={`btab ${active === t.key ? 'on' : ''}`} onClick={() => navigate(t.key)}>
          <span className="btab-label">{t.label}{t.key === 'needs' && needsCount ? <span className="badge">{needsCount}</span> : null}</span>
          <span className="btab-dot" />
        </button>
      ))}
    </nav>
  )
}

// ---- ⌘K command palette ----
type Cmd = { id: string; label: string; hint?: string; run: () => void }
export function CommandPalette({ open, setOpen, navigate, cycleTheme, openAgent, onKey }: {
  open: boolean; setOpen: (v: boolean) => void; navigate: (r: string) => void
  cycleTheme: () => void; openAgent: (seed?: string) => void; onKey: () => void
}) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (open) { setQ(''); setSel(0); setTimeout(() => inputRef.current?.focus(), 30) } }, [open])

  const base: Cmd[] = [
    { id: 'today', label: 'Go to Today', hint: 'view', run: () => navigate('today') },
    { id: 'graph', label: 'Go to Graph', hint: 'view', run: () => navigate('graph') },
    { id: 'records', label: 'Go to Records', hint: 'view', run: () => navigate('records') },
    { id: 'ops', label: 'Go to Ops', hint: 'view', run: () => navigate('ops') },
    { id: 'ask', label: 'Ask the brain', hint: 'agent', run: () => openAgent() },
    { id: 'theme', label: 'Toggle theme', hint: 'day / night / auto', run: cycleTheme },
    { id: 'key', label: 'Set API key', hint: 'auth', run: onKey },
  ]
  const ql = q.trim().toLowerCase()
  const matches = ql ? base.filter((c) => c.label.toLowerCase().includes(ql) || c.hint?.includes(ql)) : base
  // Enter with a query that matches nothing → ask the brain with it.
  const list: Cmd[] = matches.length ? matches
    : [{ id: 'ask-q', label: `Ask: “${q.trim()}”`, hint: 'agent', run: () => openAgent(q.trim()) }]

  const act = (c: Cmd) => { setOpen(false); c.run() }
  if (!open) return null
  return (
    <div className="palette-scrim" onClick={() => setOpen(false)}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input ref={inputRef} className="palette-input" placeholder="Search views · ask the brain…" value={q}
          onChange={(e) => { setQ(e.target.value); setSel(0) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(list.length - 1, s + 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(0, s - 1)) }
            else if (e.key === 'Enter') { e.preventDefault(); if (list[sel]) act(list[sel]) }
            else if (e.key === 'Escape') setOpen(false)
          }} />
        <div className="palette-list">
          {list.map((c, i) => (
            <button key={c.id} className={`palette-row ${i === sel ? 'sel' : ''}`} onMouseEnter={() => setSel(i)} onClick={() => act(c)}>
              <span>{c.label}</span>{c.hint && <span className="palette-hint mono">{c.hint}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---- API key panel ----
export function KeyPanel({ open, setOpen, current, save }: {
  open: boolean; setOpen: (v: boolean) => void; current: string; save: (k: string) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (open) setTimeout(() => ref.current?.focus(), 30) }, [open])
  if (!open) return null
  const commit = () => { save((ref.current?.value || '').trim()); setOpen(false) }
  return (
    <div className="palette-scrim" onClick={() => setOpen(false)}>
      <div className="keypanel" onClick={(e) => e.stopPropagation()}>
        <Eyebrow>your machine · your key</Eyebrow>
        <p className="muted" style={{ margin: '8px 0 12px', fontSize: 13 }}>
          Paste your <span className="mono">CAPTURE_KEY</span>. It stays in this browser and authorizes reads, capture, and ask.
        </p>
        <div className="gate">
          <input ref={ref} className="field" type="password" placeholder="paste CAPTURE_KEY" defaultValue={current}
            onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setOpen(false) }} />
          <Btn kind="primary" onClick={commit}>save</Btn>
        </div>
      </div>
    </div>
  )
}
