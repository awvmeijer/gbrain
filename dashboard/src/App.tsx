import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useTheme } from './theme'
import { api, getKey, setKey, type Health } from './api'
import { useHashRoute, useIsMobile } from './hooks'
import { DotMark, Verdict } from './kit/kit'
import { TopRail, HudRibbon, BottomTabs, CommandPalette, KeyPanel } from './chrome/chrome'
import { AgentDock } from './agent/AgentDock'
import { Today } from './views/Today'
import { Graph } from './views/Graph'
import { Records } from './views/Records'
import { Ops } from './views/Ops'
import { MobileToday, MobileNeeds, MobileSearch, MobileCapture, MobileMore } from './mobile/screens'

export default function App() {
  const { resolved, mode, setMode } = useTheme()
  const { isMobile, override, setOverride } = useIsMobile()
  const { route, navigate } = useHashRoute()
  const [key, setKeyState] = useState(getKey())
  const [health, setHealth] = useState<Health | null>(null)
  const [needsCount, setNeedsCount] = useState(0)
  const [keyOpen, setKeyOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  const [agentSeed, setAgentSeed] = useState<string>('')

  // DEV convenience: seed the key from VITE_DEV_KEY so previews show live data.
  useEffect(() => {
    const env = (import.meta as unknown as { env: Record<string, string> }).env
    if (env?.DEV && env?.VITE_DEV_KEY && !getKey()) { setKey(env.VITE_DEV_KEY); setKeyState(env.VITE_DEV_KEY) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!key) return
    let live = true
    const load = () => {
      api.health().then((h) => live && setHealth(h)).catch(() => {})
      api.needs().then((n) => live && setNeedsCount(n.proposals.length)).catch(() => {})
    }
    load()
    const t = setInterval(load, 60000)
    return () => { live = false; clearInterval(t) }
  }, [key])

  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPaletteOpen((v) => !v) }
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [])

  const cycleTheme = () => setMode(mode === 'AUTO' ? 'DAY' : mode === 'DAY' ? 'NIGHT' : 'AUTO')
  const saveKey = (k: string) => { setKey(k); setKeyState(k) }
  const openAgent = useCallback((seed?: string) => { setAgentSeed(seed || ''); setAgentOpen(true) }, [])

  let body: ReactNode
  if (!key) {
    body = (
      <div className="keygate">
        <DotMark size={64} />
        <h1 className="brand wordmark" style={{ fontSize: 34 }}>BRAINS<span className="slash">/</span></h1>
        <p className="muted">Your local knowledge brain. Paste your key to begin.</p>
        <button className="btn primary press" onClick={() => setKeyOpen(true)}>set API key</button>
      </div>
    )
  } else if (isMobile) {
    const seg = route.split('/')[0]
    const screen = seg === 'needs' ? <MobileNeeds />
      : seg === 'search' ? <MobileSearch />
        : seg === 'capture' ? <MobileCapture />
          : seg === 'more' ? <MobileMore health={health} mode={mode} cycleTheme={cycleTheme} onKey={() => setKeyOpen(true)} override={override} setOverride={setOverride} />
            : <MobileToday navigate={navigate} />
    body = (
      <div className="mobile-shell">
        <header className="mtopbar">
          <span className="brandlock"><DotMark size={20} /><span className="wordmark brand" style={{ fontSize: 16 }}>BRAINS<span className="slash">/</span></span></span>
          <span style={{ flex: 1 }} />
          <Verdict health={health} />
          <button className="chipbtn press" onClick={() => setKeyOpen(true)}>key</button>
        </header>
        <main className="mmain">{screen}</main>
        <BottomTabs route={route} navigate={navigate} needsCount={needsCount} />
        <AgentDock open={agentOpen} setOpen={setAgentOpen} mobile seedQ={agentSeed} />
      </div>
    )
  } else {
    const seg = route.split('/')[0]
    const view = seg === 'graph' ? <Graph />
      : seg === 'records' ? <Records />
        : seg === 'ops' ? <Ops />
          : <Today health={health} />
    const hud: [string, ReactNode][] = [
      ['SYS', <Verdict key="v" health={health} />],
      ['OLLAMA', health?.services.ollama ? '✓' : '×'],
      ['BRIDGE', health?.services.bridge ? '✓' : '×'],
      ['RERANK', health?.services.reranker ? '✓' : '×'],
      ['NEEDS', needsCount],
    ]
    body = (
      <div className="desktop-shell">
        <TopRail route={route} navigate={navigate} health={health} mode={mode} cycleTheme={cycleTheme} onKey={() => setKeyOpen(true)} onPalette={() => setPaletteOpen(true)} />
        <main className="deskmain">{view}</main>
        <HudRibbon items={hud} />
        <AgentDock open={agentOpen} setOpen={setAgentOpen} mobile={false} seedQ={agentSeed} />
      </div>
    )
  }

  return (
    <div className="brains-root" data-th={resolved}>
      {body}
      <CommandPalette open={paletteOpen} setOpen={setPaletteOpen} navigate={navigate} cycleTheme={cycleTheme} openAgent={openAgent} onKey={() => setKeyOpen(true)} />
      <KeyPanel open={keyOpen} setOpen={setKeyOpen} current={key} save={saveKey} />
    </div>
  )
}
