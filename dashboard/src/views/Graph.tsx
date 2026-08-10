import { useMemo, useRef, useState } from 'react'
import { api, type GraphNode, type GraphEdge } from '../api'
import { useAsync } from '../hooks'
import { Eyebrow, Tag, Card, Btn, Loading, ErrorState, hueFor } from '../kit/kit'
import { PageModal } from '../kit/PageModal'

const W = 1000, H = 640, CX = W / 2, CY = H / 2

// ---- corpus mode: deterministic phyllotaxis packing (pages clustered by source)
type Placed = GraphNode & { x: number; y: number; r: number }

function layout(nodes: GraphNode[], sources: { source: string; count: number }[]) {
  const S = sources.length || 1
  const ring = S <= 1 ? 0 : Math.min(230, 90 + S * 18)
  const hubs: Record<string, { x: number; y: number }> = {}
  sources.forEach((s, i) => {
    const a = -Math.PI / 2 + (i / S) * Math.PI * 2
    hubs[s.source] = { x: CX + ring * Math.cos(a), y: CY + ring * Math.sin(a) }
  })
  const idx: Record<string, number> = {}
  const placed: Placed[] = nodes.map((n) => {
    const hub = hubs[n.source] || { x: CX, y: CY }
    const k = (idx[n.source] = (idx[n.source] ?? -1) + 1)
    const ang = k * 2.399963 // golden angle
    const rad = 15 * Math.sqrt(k + 0.5)
    return { ...n, x: hub.x + rad * Math.cos(ang), y: hub.y + rad * Math.sin(ang), r: 5 }
  })
  return { placed, hubs }
}

// ---- relations mode: deterministic force layout with visible edges (no deps).
// Seeded from a hash of each slug so positions are stable across renders — no
// Math.random, no simulation restart on filter toggles.
type RNode = Placed & { hub?: boolean; deg?: number }
type Line = { fromSlug: string; toSlug: string; x1: number; y1: number; x2: number; y2: number; hue: string }

function hashN(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

function forceLayout(nodes: GraphNode[], edges: GraphEdge[]): { placed: RNode[]; lines: Line[] } {
  const N = nodes.length
  if (!N) return { placed: [], lines: [] }
  const P = new Map<string, { x: number; y: number; vx: number; vy: number }>()
  nodes.forEach((n) => {
    const h = hashN(n.slug)
    const a = (h % 628) / 100
    const rad = n.hub ? 30 + (h % 40) : 150 + (h % 220)
    P.set(n.slug, { x: CX + rad * Math.cos(a), y: CY + rad * Math.sin(a), vx: 0, vy: 0 })
  })
  const deg = new Map<string, number>()
  for (const e of edges) { deg.set(e.from, (deg.get(e.from) || 0) + 1); deg.set(e.to, (deg.get(e.to) || 0) + 1) }

  const ITER = 170, REP = 5200, SPRING = 0.02, REST = 64, GRAV = 0.021
  const slugs = nodes.map((n) => n.slug)
  for (let it = 0; it < ITER; it++) {
    const cool = 1 - it / ITER
    for (let i = 0; i < N; i++) {
      const a = P.get(slugs[i])!
      for (let j = i + 1; j < N; j++) {
        const b = P.get(slugs[j])!
        let dx = a.x - b.x, dy = a.y - b.y
        const d2 = dx * dx + dy * dy || 0.01
        const d = Math.sqrt(d2)
        const f = REP / d2
        const fx = (dx / d) * f, fy = (dy / d) * f
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
      }
    }
    for (const e of edges) {
      const a = P.get(e.from), b = P.get(e.to)
      if (!a || !b) continue
      const dx = b.x - a.x, dy = b.y - a.y
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01
      const f = (d - REST) * SPRING
      const fx = (dx / d) * f, fy = (dy / d) * f
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
    }
    for (const p of P.values()) {
      p.vx += (CX - p.x) * GRAV; p.vy += (CY - p.y) * GRAV
      p.x += p.vx * 0.5 * cool; p.y += p.vy * 0.5 * cool
      p.vx *= 0.82; p.vy *= 0.82
    }
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of P.values()) { minX = Math.min(minX, p.x); minY = Math.min(minY, p.y); maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y) }
  const pad = 64, s = Math.min((W - 2 * pad) / ((maxX - minX) || 1), (H - 2 * pad) / ((maxY - minY) || 1))
  const fit = (p: { x: number; y: number }) => ({ x: pad + (p.x - minX) * s, y: pad + (p.y - minY) * s })
  const placed: RNode[] = nodes.map((n) => {
    const f = fit(P.get(n.slug)!)
    const d = deg.get(n.slug) || 0
    const r = n.hub ? Math.min(17, 7 + Math.sqrt(d) * 1.4) : 3.6
    return { ...n, x: f.x, y: f.y, r }
  })
  const pm = new Map(placed.map((p) => [p.slug, p]))
  const lines: Line[] = []
  for (const e of edges) {
    const a = pm.get(e.from), b = pm.get(e.to)
    if (a && b) lines.push({ fromSlug: e.from, toSlug: e.to, x1: a.x, y1: a.y, x2: b.x, y2: b.y, hue: hueFor(a.source) })
  }
  return { placed, lines }
}

export function Graph() {
  const [seed, setSeed] = useState('')
  const [q, setQ] = useState('')
  const { data, err, loading } = useAsync(() => api.graph(seed), [seed])
  const [filter, setFilter] = useState<Set<string>>(new Set())
  const [sel, setSel] = useState<RNode | null>(null)
  const [openSlug, setOpenSlug] = useState('')
  const [view, setView] = useState({ k: 1, x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number } | null>(null)

  const isRel = data?.mode === 'relations'
  const sources = data?.sources || []

  // Corpus layout re-runs on filter (cheap). Relation layout runs once per data
  // load (expensive O(n²) sim) — filtering only hides, keeping positions stable.
  const rel = useMemo(() => (isRel ? forceLayout(data!.nodes, data!.edges) : null), [isRel, data])
  const visibleNodes = useMemo(
    () => (data?.nodes || []).filter((n) => filter.size === 0 || filter.has(n.source)),
    [data, filter],
  )
  const cor = useMemo(
    () => (!isRel ? layout(visibleNodes, sources.filter((s) => filter.size === 0 || filter.has(s.source))) : null),
    [isRel, visibleNodes, sources, filter],
  )

  const active = (src: string) => filter.size === 0 || filter.has(src)
  const relNodes = (rel?.placed || []).filter((n) => active(n.source))
  const relLines = (rel?.lines || []).filter((l) => active(l.fromSlug.split('/')[0]) && active(l.toSlug.split('/')[0]))

  const toggle = (s: string) => setFilter((f) => { const n = new Set(f); n.has(s) ? n.delete(s) : n.add(s); return n })
  const focus = (s: string) => { setSel(null); setView({ k: 1, x: 0, y: 0 }); setFilter(new Set()); setSeed(s) }
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    setView((v) => ({ ...v, k: Math.max(0.4, Math.min(4, v.k * (e.deltaY < 0 ? 1.12 : 0.89))) }))
  }
  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX - view.x, y: e.clientY - view.y } }
  const onMove = (e: React.MouseEvent) => { if (drag.current) setView((v) => ({ ...v, x: e.clientX - drag.current!.x, y: e.clientY - drag.current!.y })) }
  const onUp = () => { drag.current = null }

  if (loading) return <div className="view"><Loading /></div>
  if (err) return <div className="view"><ErrorState err={err} /></div>

  return (
    <div className="view view-fade graph">
      <div className="graph-canvas" onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" width="100%" height="100%">
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`} style={{ transformOrigin: 'center' }}>
            {isRel ? (
              <>
                {relLines.map((l, i) => (
                  <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={l.hue} strokeWidth={0.8} opacity={0.16} />
                ))}
                {relNodes.map((n) => (
                  <g key={n.slug}>
                    <circle cx={n.x} cy={n.y} r={n.r} fill={hueFor(n.source)}
                      opacity={sel && sel.slug === n.slug ? 1 : n.hub ? 0.92 : 0.62} className="gnode"
                      onClick={(e) => { e.stopPropagation(); setSel(n) }} />
                    {n.hub && <text x={n.x} y={n.y - n.r - 5} textAnchor="middle" className="glabel">{n.title}</text>}
                  </g>
                ))}
                {sel && <circle className="ghalo" cx={sel.x} cy={sel.y} r={sel.r + 6} fill="none" />}
              </>
            ) : (
              <>
                {sources.filter((s) => active(s.source)).map((s) => {
                  const h = cor?.hubs[s.source]; if (!h) return null
                  return (
                    <g key={s.source}>
                      <circle cx={h.x} cy={h.y} r={11} fill={hueFor(s.source)} opacity={0.85} />
                      <text x={h.x} y={h.y - 18} textAnchor="middle" className="glabel">{s.source} · {s.count}</text>
                    </g>
                  )
                })}
                {(cor?.placed || []).map((n) => (
                  <circle key={n.slug} cx={n.x} cy={n.y} r={n.r} fill={hueFor(n.source)}
                    opacity={sel && sel.slug === n.slug ? 1 : 0.7} className="gnode"
                    onClick={(e) => { e.stopPropagation(); setSel(n as RNode) }} />
                ))}
                {sel && <circle className="ghalo" cx={sel.x} cy={sel.y} r={13} fill="none" />}
              </>
            )}
          </g>
        </svg>

        <div className="graph-zoom">
          <button className="chipbtn press" onClick={() => setView((v) => ({ ...v, k: Math.min(4, v.k * 1.2) }))}>+</button>
          <button className="chipbtn press" onClick={() => setView((v) => ({ ...v, k: Math.max(0.4, v.k * 0.83) }))}>−</button>
          <button className="chipbtn press" onClick={() => setView({ k: 1, x: 0, y: 0 })}>⟲</button>
        </div>
      </div>

      <aside className="graph-panel">
        <Eyebrow>{isRel ? 'relation graph' : 'corpus map'}</Eyebrow>
        <p className="footnote" style={{ margin: '6px 0 12px' }}>
          {isRel
            ? (seed ? <>Focused on <span className="mono">{seed}</span>.</> : 'The densest hubs and what links to them. Click a node to inspect or focus.')
            : 'Pages clustered by source. Relation edges appear here as the brain extracts links.'}
        </p>
        <form className="gsearch" onSubmit={(e) => { e.preventDefault(); if (q.trim()) focus(q.trim()) }} style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
          <input className="ginput" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="seed a slug — e.g. ticker/nvda" style={{ flex: 1 }} />
          <Btn kind="ghost" type="submit">go</Btn>
          {seed && <Btn kind="ghost" onClick={() => { setQ(''); focus('') }}>reset</Btn>}
        </form>
        <div className="chips" style={{ marginBottom: 16 }}>
          {sources.map((s) => (
            <button key={s.source} className={`chip press ${filter.size && !filter.has(s.source) ? 'off' : ''}`}
              onClick={() => toggle(s.source)} style={{ borderColor: hueFor(s.source) }}>
              <span className="dot-src" style={{ background: hueFor(s.source) }} />{s.source}<span className="mono" style={{ opacity: 0.6 }}> {s.count}</span>
            </button>
          ))}
        </div>
        {sel ? (
          <Card className="ink" style={{ margin: 0 }}>
            <Tag dot tone="live">{sel.source}{sel.hub && sel.deg ? ` · ${sel.deg} links` : ''}</Tag>
            <div className="display" style={{ fontSize: 15, margin: '10px 0' }}>{sel.title}</div>
            <div className="mono footnote" style={{ wordBreak: 'break-all' }}>{sel.slug}</div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <Btn kind="primary" onClick={() => setOpenSlug(sel.slug)}>open page</Btn>
              {isRel && <Btn kind="ghost" onClick={() => focus(sel.slug)}>focus here</Btn>}
            </div>
          </Card>
        ) : <p className="muted">Click a node to inspect.</p>}
      </aside>

      {openSlug && <PageModal slug={openSlug} onClose={() => setOpenSlug('')} />}
    </div>
  )
}
