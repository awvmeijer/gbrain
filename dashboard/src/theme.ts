import { useEffect, useState } from 'react'

export type ThemeMode = 'AUTO' | 'DAY' | 'NIGHT'
export type Resolved = 'day' | 'night'

const LS_KEY = 'brain.theme' // shared with the PWA (values auto|day|night)

export function useTheme(): { mode: ThemeMode; resolved: Resolved; setMode: (m: ThemeMode) => void } {
  const [mode, setModeState] = useState<ThemeMode>(() => {
    try {
      const v = (localStorage.getItem(LS_KEY) || 'auto').toUpperCase()
      return v === 'DAY' || v === 'NIGHT' ? (v as ThemeMode) : 'AUTO'
    } catch {
      return 'AUTO'
    }
  })
  const [osDark, setOsDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const fn = (e: MediaQueryListEvent) => setOsDark(e.matches)
    mq.addEventListener('change', fn)
    return () => mq.removeEventListener('change', fn)
  }, [])

  const resolved: Resolved = mode === 'AUTO' ? (osDark ? 'night' : 'day') : mode === 'DAY' ? 'day' : 'night'

  useEffect(() => {
    document.documentElement.dataset.th = resolved
    document.documentElement.style.background = resolved === 'day' ? '#DBDCDD' : '#111213'
    // PWA-compatible event so any shared canvas code can re-read palette.
    window.dispatchEvent(new CustomEvent('themechange', { detail: { theme: resolved } }))
  }, [resolved])

  const setMode = (m: ThemeMode) => {
    setModeState(m)
    try {
      localStorage.setItem(LS_KEY, m.toLowerCase())
    } catch {
      /* ignore */
    }
  }
  return { mode, resolved, setMode }
}
