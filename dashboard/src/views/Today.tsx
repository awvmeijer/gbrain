import { useState, type ReactNode } from 'react'
import { api, type Health, type Proposal } from '../api'
import { useAsync } from '../hooks'
import { Eyebrow, KpiBlock, Stadium, Fig, Card, Btn, Tag, Markdown, Loading, ErrorState, Empty, hueFor } from '../kit/kit'
import { Findings } from '../kit/Findings'

// wrap numbers / $amounts / % in an underlined Fig for the stadium idiom
function withFigures(text: string): ReactNode[] {
  const parts = text.split(/(\$?\d[\d,.]*%?)/g)
  return parts.map((p, i) => (/^\$?\d[\d,.]*%?$/.test(p) ? <Fig key={i}>{p}</Fig> : <span key={i}>{p}</span>))
}

function digestHighlights(md: string): string[] {
  const body = md.replace(/^---\n[\s\S]*?\n---\n?/, '')
  const lines = body.split('\n').map((l) => l.trim())
  const bullets = lines
    .filter((l) => /^[-*]\s+/.test(l))
    .map((l) => l.replace(/^[-*]\s+/, '').replace(/\*\*/g, '').replace(/`/g, ''))
    .filter((l) => l.length > 8)
  return bullets.slice(0, 5)
}

export function Today({ health, date }: { health: Health | null; date?: string }) {
  const today = useAsync(() => api.today(), [])
  const stats = useAsync(() => api.stats(), [])
  const needs = useAsync(() => api.needs(), [])
  const recent = useAsync(() => api.pages('', 8, 'updated_desc'), [])

  const [busy, setBusy] = useState('')
  const s = stats.data
  const proposals = needs.data?.proposals ?? []
  const pending = proposals.length
  const staleFeeds = health?.feeds.filter((f) => f.stale).length ?? 0
  const highlights = today.data?.content ? digestHighlights(today.data.content) : []
  const decide = async (p: Proposal, d: 'approve' | 'reject') => {
    setBusy(p.slug)
    try { await api.decide(p.slug, d); needs.reload() } finally { setBusy('') }
  }

  const now = new Date()
  const dstr = now.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })

  return (
    <div className="view view-fade today">
      <div className="today-hero">
        <Eyebrow>daily digest · {today.data?.date || date || dstr}</Eyebrow>
        <h1 className="display today-date">{dstr}</h1>
      </div>

      <div className="kpi-strip">
        <KpiBlock label="pages" value={s?.pages ?? 0} count sub={s ? `${s.embedded} embedded` : ''} />
        <KpiBlock label="needs you" value={pending} count tone={pending ? 'warn' : 'ok'} sub={pending ? 'pending' : 'clear'} />
        <KpiBlock label="feeds" value={health ? health.feeds.length - staleFeeds : 0} count tone={staleFeeds ? 'warn' : 'live'} sub={staleFeeds ? `${staleFeeds} stale` : 'fresh'} />
        <KpiBlock label="tags" value={s?.tags ?? 0} count sub="topics" />
      </div>

      <Findings />

      {highlights.length > 0 && (
        <div className="stadiums">
          {highlights.map((h, i) => <Stadium key={i}>{withFigures(h)}</Stadium>)}
        </div>
      )}

      {pending > 0 && (
        <Card className="ink approvals">
          <div className="mhead"><Eyebrow>needs you</Eyebrow><Tag tone="warn">{pending} pending</Tag></div>
          <div className="approve-list">
            {proposals.slice(0, 4).map((p) => (
              <div key={p.slug} className="approve-row">
                <div className="approve-meta">
                  <span className="mono act">{p.action || 'proposal'}</span>
                  <span className="mono target"> → {p.target}</span>
                  <div className="approve-why">{p.rationale || p.title}</div>
                </div>
                <div className="approve-btns">
                  <Btn kind="primary" disabled={!!busy} onClick={() => decide(p, 'approve')}>approve</Btn>
                  <Btn kind="ghost" disabled={!!busy} onClick={() => decide(p, 'reject')}>reject</Btn>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="today-grid">
        <Card className="ink">
          <Eyebrow>full digest</Eyebrow>
          {today.loading ? <Loading /> : today.err ? <ErrorState err={today.err} />
            : today.data?.content ? <div style={{ marginTop: 10 }}><Markdown src={today.data.content} /></div>
              : <Empty>No digest yet — the daily run writes one each morning.</Empty>}
        </Card>

        <Card className="ink recent-card">
          <Eyebrow>latest in</Eyebrow>
          {recent.loading ? <Loading /> : recent.data?.pages.length ? (
            <div className="recent-list" style={{ marginTop: 8 }}>
              {recent.data.pages.map((p) => (
                <div key={p.slug} className="recent-row">
                  <span className="dot-src" style={{ background: hueFor(p.slug.split('/')[0]) }} />
                  <span className="recent-title">{p.title}</span>
                  <span className="mono recent-src">{p.slug.split('/')[0]}</span>
                </div>
              ))}
            </div>
          ) : <Empty>Nothing ingested yet.</Empty>}
        </Card>
      </div>
    </div>
  )
}
