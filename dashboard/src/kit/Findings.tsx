import { useState } from 'react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Eyebrow, Card, Tag } from './kit'
import { PageModal } from './PageModal'

// The "1+1=3" front door: ranked convergence findings. Hidden when none.
export function Findings() {
  const { data, loading } = useAsync(() => api.findings(), [])
  const [open, setOpen] = useState('')
  const items = data?.findings || []
  if (loading || !items.length) return null
  return (
    <Card className="ink findings">
      <div className="mhead"><Eyebrow>convergence · needs your read</Eyebrow><Tag tone="live" dot>{items.length}</Tag></div>
      <div className="finding-list">
        {items.map((f) => (
          <button key={f.slug} className="finding-row press" onClick={() => setOpen(f.slug)}>
            <span className="kpi finding-score">{f.score.toFixed(2)}</span>
            <span className="finding-ticker">${f.ticker}</span>
            <span className="mono finding-classes">{f.classes.join(' + ')}</span>
            <span className="arr">→</span>
          </button>
        ))}
      </div>
      {open && <PageModal slug={open} onClose={() => setOpen('')} />}
    </Card>
  )
}
