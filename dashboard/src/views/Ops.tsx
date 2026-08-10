import { api } from '../api'
import { useAsync } from '../hooks'
import { Eyebrow, KpiBlock, Card, Tag, Loading, ErrorState, Empty } from '../kit/kit'

export function Ops() {
  const { data, err, loading } = useAsync(() => api.ops(), [])
  if (loading) return <div className="view"><Loading /></div>
  if (err) return <div className="view"><ErrorState err={err} /></div>
  if (!data) return null
  const { health, stats, detail, jobs } = data
  const svc = (name: string, up: boolean) => <Tag tone={up ? 'live' : 'err'} dot key={name}>{name}</Tag>

  return (
    <div className="view view-fade ops">
      <div className="kpi-strip wide">
        <KpiBlock label="health" value={detail.score != null ? `${detail.score}/${detail.score_max}` : '—'} tone={detail.score != null && detail.score >= 8 ? 'live' : 'warn'} sub="composite" />
        <KpiBlock label="pages" value={stats.pages} count sub={`${stats.chunks} chunks`} />
        <KpiBlock label="embedded" value={detail.embed_coverage != null ? `${detail.embed_coverage}%` : '—'} tone={detail.embed_coverage === 100 ? 'live' : 'warn'} sub={`${detail.missing_embeddings} missing`} />
        <KpiBlock label="orphans" value={detail.orphan_pages} count sub="no inbound links" tone={detail.orphan_pages > 0 ? 'warn' : 'ok'} />
        <KpiBlock label="links" value={stats.links} count sub="relations" tone={stats.links === 0 ? 'warn' : 'ok'} />
      </div>

      <div className="ops-grid">
        <Card className="ink">
          <Eyebrow>services</Eyebrow>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
            {svc('ollama', health.services.ollama)}
            {svc('bridge', health.services.bridge)}
            {svc('reranker', health.services.reranker)}
          </div>
          <Eyebrow style={{ marginTop: 18 }}>feed freshness</Eyebrow>
          <table className="dt" style={{ marginTop: 8 }}>
            <thead><tr><th>source</th><th className="num">age (h)</th><th style={{ width: 90 }}>state</th></tr></thead>
            <tbody>
              {health.feeds.map((f) => (
                <tr key={f.source}>
                  <td className="mono">{f.source}</td>
                  <td className="num">{f.age_hours ?? '—'}</td>
                  <td>{f.stale ? <Tag tone="warn" dot>stale</Tag> : <Tag tone="live" dot>fresh</Tag>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card className="ink">
          <Eyebrow>job queue</Eyebrow>
          {jobs.length ? (
            <table className="dt" style={{ marginTop: 8 }}>
              <thead><tr><th>id</th><th>status</th></tr></thead>
              <tbody>{jobs.map((j) => <tr key={j.id}><td className="mono">{j.id}</td><td>{j.status}</td></tr>)}</tbody>
            </table>
          ) : <Empty>No jobs queued. Agent activity will appear here once minions run.</Empty>}

          <Eyebrow style={{ marginTop: 18 }}>corpus by type</Eyebrow>
          <div className="kv" style={{ marginTop: 8 }}>
            {Object.entries(stats.by_type).map(([k, n]) => (
              <div key={k} style={{ display: 'contents' }}>
                <span className="k">{k}</span><span className="v">{n}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
