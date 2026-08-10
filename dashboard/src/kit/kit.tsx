import { useEffect, useRef, useState, type ReactNode, type CSSProperties } from 'react'
import { renderMarkdown } from '../md'
import type { Health } from '../api'

export { DotMark } from './DotMark'

// ---- micro type ----
export function Eyebrow({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <span className="eyebrow" style={style}><span className="sq" />{children}</span>
}

export type Tone = 'ok' | 'live' | 'warn' | 'err'
export function Tag({ children, tone, solid, dot }: { children: ReactNode; tone?: Tone; solid?: boolean; dot?: boolean }) {
  return <span className={`tag${tone ? ` ${tone}` : ''}${solid ? ' solid' : ''}`}>{dot && <span className="dot" />}{children}</span>
}

export function SplitTag({ a, b }: { a: ReactNode; b: ReactNode }) {
  return <span className="splittag"><span>{a}</span><span>{b}</span></span>
}

export function Stadium({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div className="stadium" style={style}><span className="arr">→</span><span style={{ flex: 1, minWidth: 0 }}>{children}</span></div>
}
export function Fig({ children }: { children: ReactNode }) { return <u className="fig">{children}</u> }

export function Card({ children, className = '', style, onClick }: { children: ReactNode; className?: string; style?: CSSProperties; onClick?: () => void }) {
  return <div className={`card ${className}`} style={style} onClick={onClick}>{children}</div>
}

export function Btn({ children, kind = 'ghost', onClick, disabled, style, title, type }: {
  children: ReactNode; kind?: 'primary' | 'ghost'; onClick?: () => void; disabled?: boolean; style?: CSSProperties; title?: string; type?: 'button' | 'submit'
}) {
  return <button type={type || 'button'} title={title} className={`btn press ${kind}`} onClick={onClick} disabled={disabled} style={style}>{children}</button>
}

// ---- count-up (reduced-motion aware) ----
function useCountUp(target: number, ms = 650): number {
  const [v, setV] = useState(0)
  const raf = useRef(0)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || target <= 0) { setV(target); return }
    const t0 = performance.now()
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / ms)
      setV(Math.round((1 - Math.pow(1 - p, 3)) * target))
      if (p < 1) raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf.current)
  }, [target, ms])
  return v
}

export function KpiBlock({ label, value, sub, tone, count }: { label: string; value: number | string; sub?: string; tone?: Tone; count?: boolean }) {
  const numeric = typeof value === 'number' && count
  const shown = useCountUp(numeric ? (value as number) : 0)
  const display = numeric ? shown.toLocaleString() : value
  const color = tone === 'warn' ? 'var(--warn)' : tone === 'err' ? 'var(--error)' : tone === 'live' ? 'var(--mint)' : undefined
  return (
    <div className="kpiblock">
      <Eyebrow>{label}</Eyebrow>
      <div className="kpi kpival" style={{ color }}>{display}</div>
      {sub && <div className="footnote mono">{sub}</div>}
    </div>
  )
}

export function StatusHUD({ items }: { items: [string, ReactNode][] }) {
  return (
    <div className="hud mono">
      {items.map(([k, v], i) => <span key={i} className="hud-item">{k} <b>{v}</b></span>)}
    </div>
  )
}

export function Verdict({ health }: { health: Health | null }) {
  const v = health?.verdict
  if (!v) return null
  return <span className={`verdict ${v}`}><span className="dot" />{v}</span>
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <p className="muted">{label}</p>
}
export function ErrorState({ err }: { err: string }) {
  const auth = /401/.test(err)
  return <p className="err">{auth ? '401 — set your API key (tap “key”).' : err}</p>
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty"><Eyebrow>nothing here</Eyebrow><p className="muted" style={{ marginTop: 8 }}>{children}</p></div>
}

export function Markdown({ src }: { src: string }) {
  return <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(src) }} />
}

// source → kind hue (for corpus map + records facets)
export const SOURCE_HUE: Record<string, string> = {
  x: 'var(--k-person)', telegram: 'var(--k-topic)', youtube: 'var(--k-repo)',
  discord: 'var(--k-agent)', capture: 'var(--k-project)', digests: 'var(--k-decision)',
  proposals: 'var(--azure-bright)', synthesis: 'var(--k-company)', rss: 'var(--k-ticker)',
  // relation-graph slug prefixes (nodes are typed by their slug's first segment)
  ticker: 'var(--k-ticker)', filings: 'var(--k-decision)', permit: 'var(--k-company)',
  finding: 'var(--azure-bright)', paper: 'var(--k-repo)', session: 'var(--k-agent)',
  reflection: 'var(--mint)', legacy: 'var(--k-topic)', note: 'var(--k-project)',
}
const HUE_PALETTE = [
  'var(--k-person)', 'var(--k-topic)', 'var(--k-repo)', 'var(--k-agent)', 'var(--k-project)',
  'var(--k-decision)', 'var(--k-company)', 'var(--k-ticker)', 'var(--azure-bright)', 'var(--mint)',
]
// Named prefixes win; anything else gets a stable hashed hue so every source is
// visually distinct (no more everything-falls-back-to-one-gray).
export function hueFor(source: string): string {
  if (SOURCE_HUE[source]) return SOURCE_HUE[source]
  let h = 0
  for (let i = 0; i < source.length; i++) h = (h * 31 + source.charCodeAt(i)) >>> 0
  return HUE_PALETTE[h % HUE_PALETTE.length]
}
