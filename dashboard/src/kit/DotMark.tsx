import type { CSSProperties, ReactNode } from 'react'

// BRAINS/ master mark — the "Shape Matrix" (per the brand Identity).
// A 3×3 grid: six dim circles = MEMORY at rest; the lit anti-diagonal (the "/"
// of the wordmark) reads □ → ✕ → △ = OBJECTS → RELATIONS → AGENTS.
// Below 32px the glyphs reduce to plain dots (state-only). `thinking` runs a
// travelling opacity wave (the AI-agent "working" signal), paced by --bm-cycle.
const U = 100
const PAD = U * 0.21
const STEP = (U - PAD * 2) / 2
const R = U * 0.082
const RX = U * 0.2237
const CREAM = '#F4F2EC'
const DIAG: Record<number, 'square' | 'x' | 'tri'> = { 2: 'tri', 4: 'x', 6: 'square' } // top-right → center → bottom-left

function cell(shape: 'circle' | 'square' | 'x' | 'tri', cx: number, cy: number, opacity: number, delay: number | null, key: number): ReactNode {
  const style: CSSProperties = { opacity }
  if (delay != null) style.animationDelay = `${delay}s`
  const cls = 'd'
  if (shape === 'square') return <rect key={key} className={cls} x={cx - R} y={cy - R} width={R * 2} height={R * 2} fill={CREAM} style={style} />
  if (shape === 'tri') return <polygon key={key} className={cls} points={`${cx},${cy - R * 1.12} ${cx + R * 1.12},${cy + R * 0.92} ${cx - R * 1.12},${cy + R * 0.92}`} fill={CREAM} style={style} />
  if (shape === 'x') {
    const w = R * 0.62
    return (
      <g key={key} className={cls} transform={`rotate(45 ${cx} ${cy})`} style={style}>
        <rect x={cx - R * 1.18} y={cy - w / 2} width={R * 2.36} height={w} fill={CREAM} />
        <rect x={cx - w / 2} y={cy - R * 1.18} width={w} height={R * 2.36} fill={CREAM} />
      </g>
    )
  }
  return <circle key={key} className={cls} cx={cx} cy={cy} r={R} fill={CREAM} style={style} />
}

export function DotMark({
  size = 28,
  thinking = false,
  tile = true,
  title = 'brains',
}: {
  size?: number
  thinking?: boolean
  tile?: boolean
  title?: string
}) {
  const pure = size < 32 // glyphs reduce to dots at small sizes
  const cells: ReactNode[] = []
  for (let i = 0; i < 9; i++) {
    const cx = PAD + (i % 3) * STEP
    const cy = PAD + Math.floor(i / 3) * STEP
    const glyph = DIAG[i]
    const lit = !!glyph
    const shape = pure || !glyph ? 'circle' : glyph
    const delay = thinking ? ((i % 3) + Math.floor(i / 3)) / 4 * 5.6 : null // wave sweeps corner→corner
    cells.push(cell(shape, cx, cy, lit ? 1 : 0.22, delay, i))
  }
  return (
    <span
      className={`dotmark${thinking ? ' thinking' : ''}`}
      style={{ width: size, height: size }}
      role="img"
      aria-label={thinking ? `${title} — thinking` : title}
    >
      <svg viewBox={`0 0 ${U} ${U}`} xmlns="http://www.w3.org/2000/svg">
        {tile && <rect width={U} height={U} rx={RX} fill="#16181A" />}
        {cells}
      </svg>
    </span>
  )
}
