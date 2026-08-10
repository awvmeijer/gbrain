import { useCallback, useEffect, useState } from 'react'

// ---- mobile detection: UA + viewport, with a manual override the user can
// flip (stored in localStorage so it survives reloads). ----
const VIEW_LS = 'view.mode' // 'auto' | 'mobile' | 'desktop'
export type ViewOverride = 'auto' | 'mobile' | 'desktop'

function coarse(): boolean {
  const ua = /iPhone|iPod|Android.*Mobile|Windows Phone/i.test(navigator.userAgent)
  const narrow = window.matchMedia('(max-width: 768px)').matches
  const touch = window.matchMedia('(pointer: coarse)').matches
  return ua || (narrow && touch) || narrow
}

export function useIsMobile(): { isMobile: boolean; override: ViewOverride; setOverride: (v: ViewOverride) => void } {
  const [override, setOv] = useState<ViewOverride>(() => {
    try { return (localStorage.getItem(VIEW_LS) as ViewOverride) || 'auto' } catch { return 'auto' }
  })
  const [narrow, setNarrow] = useState(coarse)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const fn = () => setNarrow(coarse())
    mq.addEventListener('change', fn)
    window.addEventListener('resize', fn)
    return () => { mq.removeEventListener('change', fn); window.removeEventListener('resize', fn) }
  }, [])
  const setOverride = useCallback((v: ViewOverride) => {
    setOv(v); try { localStorage.setItem(VIEW_LS, v) } catch { /* ignore */ }
  }, [])
  const isMobile = override === 'mobile' ? true : override === 'desktop' ? false : narrow
  return { isMobile, override, setOverride }
}

// ---- tiny hash router (StaticFiles has no SPA catch-all → hash routing). ----
export function useHashRoute(): { route: string; navigate: (to: string) => void } {
  const read = () => (location.hash.replace(/^#\/?/, '') || 'today')
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const fn = () => setRoute(read())
    window.addEventListener('hashchange', fn)
    return () => window.removeEventListener('hashchange', fn)
  }, [])
  const navigate = useCallback((to: string) => {
    const clean = to.replace(/^#?\/?/, '')
    if (clean !== read()) location.hash = `/${clean}`
    else setRoute(clean)
  }, [])
  return { route, navigate }
}

// ---- generic async loader ----
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const reload = useCallback(() => {
    setLoading(true); setErr('')
    fn().then(setData).catch((e) => setErr(String(e.message || e))).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(reload, [reload])
  return { data, err, loading, reload }
}
