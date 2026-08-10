import { useMemo, useState } from 'react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Eyebrow, SplitTag, Card, Markdown, Loading, ErrorState, Empty, hueFor } from '../kit/kit'

export function Records() {
  const pages = useAsync(() => api.pages('', 300, 'updated_desc'), [])
  const [source, setSource] = useState('')
  const [q, setQ] = useState('')
  const [ran, setRan] = useState('')
  const search = useAsync(() => (ran.trim().length > 1 ? api.search(ran, 30) : Promise.resolve({ hits: [] })), [ran])
  const [sel, setSel] = useState('')
  const page = useAsync(() => (sel ? api.page(sel) : Promise.resolve({ slug: '', content: '' })), [sel])

  const sources = useMemo(() => {
    const c: Record<string, number> = {}
    for (const p of pages.data?.pages || []) { const s = p.slug.split('/')[0]; c[s] = (c[s] || 0) + 1 }
    return Object.entries(c).sort((a, b) => b[1] - a[1])
  }, [pages.data])

  const searching = ran.trim().length > 1
  const rows = (pages.data?.pages || []).filter((p) => !source || p.slug.startsWith(`${source}/`))

  return (
    <div className="view view-fade records3">
      <aside className="rail rail-tight">
        <Eyebrow>source</Eyebrow>
        <div className="facet-group">
          <div className={`facet-row ${!source ? 'on' : ''}`} onClick={() => setSource('')}>
            <span>all</span><span className="ct">{pages.data?.pages.length ?? 0}</span>
          </div>
          {sources.map(([s, n]) => (
            <div key={s} className={`facet-row ${source === s ? 'on' : ''}`} onClick={() => setSource(source === s ? '' : s)}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><span className="dot-src" style={{ background: hueFor(s) }} />{s}</span>
              <span className="ct">{n}</span>
            </div>
          ))}
        </div>
      </aside>

      <section className="work">
        <div className="worktop">
          <input className={`field grow ${searching ? 'active' : ''}`} placeholder="Search the brain…" value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') setRan(q); if (e.key === 'Escape') { setQ(''); setRan('') } }} />
          <span className="eyebrow" style={{ marginLeft: 12 }}>{searching ? `${search.data?.hits.length ?? 0} ranked hits` : `${rows.length} records`}</span>
        </div>

        <div className="tablewrap">
          {searching ? (
            search.loading ? <Loading /> : search.err ? <ErrorState err={search.err} /> : (
              <table className="dt">
                <thead><tr><th style={{ width: 64 }}>score</th><th>title</th><th style={{ width: 120 }}>source</th></tr></thead>
                <tbody>
                  {(search.data?.hits || []).map((h) => (
                    <tr key={h.slug} className={sel === h.slug ? 'sel' : ''} onClick={() => setSel(h.slug)}>
                      <td className="num">{h.score.toFixed(2)}</td>
                      <td>{h.snippet.replace(/^[-#\s]+/, '').slice(0, 80) || h.slug}</td>
                      <td className="mono" style={{ color: 'var(--t-sec)' }}>{h.slug.split('/')[0]}</td>
                    </tr>
                  ))}
                  {(search.data?.hits.length ?? 0) === 0 && <tr><td colSpan={3} className="muted" style={{ padding: 16 }}>No hits.</td></tr>}
                </tbody>
              </table>
            )
          ) : pages.loading ? <Loading /> : pages.err ? <ErrorState err={pages.err} /> : (
            <table className="dt">
              <thead><tr><th style={{ width: 96 }}>date</th><th style={{ width: 110 }}>source</th><th>title</th></tr></thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.slug} className={sel === p.slug ? 'sel' : ''} onClick={() => setSel(p.slug)}>
                    <td className="num" style={{ textAlign: 'left' }}>{p.date}</td>
                    <td className="mono" style={{ color: 'var(--t-sec)' }}>{p.slug.split('/')[0]}</td>
                    <td>{p.title}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={3} className="muted" style={{ padding: 16 }}>No records.</td></tr>}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <aside className="detail">
        {!sel ? <Empty>Select a record to read it.</Empty> : page.loading ? <Loading /> : page.err ? <ErrorState err={page.err} /> : (
          <Card className="ink" style={{ margin: 0 }}>
            <SplitTag a="ep" b={sel.split('/')[0]} />
            <div style={{ marginTop: 12 }}><Markdown src={page.data?.content || ''} /></div>
          </Card>
        )}
      </aside>
    </div>
  )
}
