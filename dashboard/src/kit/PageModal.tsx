import { useAsync } from '../hooks'
import { api } from '../api'
import { Eyebrow, Markdown, Loading, ErrorState } from './kit'

export function PageModal({ slug, onClose }: { slug: string; onClose: () => void }) {
  const { data, err, loading } = useAsync(() => api.page(slug), [slug])
  return (
    <div className="palette-scrim" onClick={onClose}>
      <div className="pagemodal ink" onClick={(e) => e.stopPropagation()}>
        <div className="agenthead">
          <Eyebrow>{slug}</Eyebrow><span style={{ flex: 1 }} />
          <button className="agentx" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="pagemodal-body">
          {loading ? <Loading /> : err ? <ErrorState err={err} /> : <Markdown src={data?.content || ''} />}
        </div>
      </div>
    </div>
  )
}
