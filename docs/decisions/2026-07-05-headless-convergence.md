# Decision: converge on headless gbrain (brains-port), archive ~/brain

**Date:** 2026-07-05 · **Status:** accepted · **Supersedes:** the 2026-06 "keep the brain" call (see § Why the reversal)

## Decision

Converge all brain investment on **this repo** — the `brains-port` fork of
[garrytan/gbrain](https://github.com/garrytan/gbrain) — in a **headless posture**:

- No new UI work. The v3 React dashboard (`admin/`) is frozen; built assets keep
  being served by the capture sidecar, but new capability lands ONLY as
  skills/recipes/sidecars (thin harness, thick skills).
- Reasoning stays on the **Claude Max flat-fee plan** via `sidecars/max-bridge/`
  (port 8789, OpenAI-compatible shim over claude-agent-sdk, CLI-inherited auth,
  `cost_per_1m_* = 0` in `src/core/ai/recipes/max-bridge.ts`). Embeddings stay on
  **Ollama bge-m3** (local, free); reranking stays on the **llama.cpp
  bge-reranker-v2-m3** sidecar (port 8081, local, free).
- The old from-scratch app at `~/brain` (Python/SQLite/FastAPI/PWA) is archived:
  read-only API on :8790, DB retained, no schedulers.
- **No fresh vanilla install.** The fork's core delta vs upstream v0.42.53.0
  (pin `814258dd`, `FORK_PIN.md`) is one small provider-recipe file plus its
  registration line in `src/core/ai/recipes/index.ts`; everything else is
  additive (`sidecars/`, `recipes/`, `skills/`, `infra/`). A reinstall per
  upstream INSTALL_FOR_AGENTS.md would cost a full re-import/re-embed of a live
  244MB PGLite corpus for zero retrieval gain.

## Why the reversal vs June

The 2026-06 experiment (`~/brain-gbrain-exp/experiments/gbrain/README.md`,
branch `experiment/gbrain-backend`) concluded "keep the brain" because,
embedder-controlled (bge-m3 both sides), gbrain's base retrieval did **not**
beat the brain's hybrid+RRF. That verdict answered a *retrieval-quality*
question and still stands. The July decision answers a different question —
*where should ongoing engineering effort go* — and the axis is harness
economics, not recall:

- The fork's harness is contract-first and thin (~90 operations in
  `src/core/operations.ts`; CLI + MCP generated from it). The old brain pairs
  every markdown skill with a 300–400-line Python agent.
- Upstream ships velocity for free (dream cycle, schema packs, engine parity,
  70+ skills); the old brain pays for every feature by hand.
- Max-bridge + local embed/rerank made the fork's marginal reasoning cost $0.
- Forge (in development) will own "do work" skills; keeping two bespoke
  app-layers (PWA + dashboard) duplicates effort three ways. Headless memory
  engine + skills is the non-redundant scope.

## State of the world (verified 2026-07-05)

| Port | Service | Status |
|------|---------|--------|
| 8787 | capture sidecar (FastAPI): phone/web capture, `/api/context`, `/api/session-record`, v3 dashboard | live (launchd `com.brains.capture`) |
| 8789 | max-bridge (Claude Max CLI shim) | live (`com.brains.max-bridge`) |
| 8081 | reranker (llama-server, bge-reranker-v2-m3) | live (`com.brains.reranker`) |
| 11434 | Ollama (bge-m3 embeddings; local reasoning tier Gemma 4) | live (`com.brain.ollama` — shared, kept) |
| 8790 | OLD ~/brain FastAPI | read-only archive |

- Data: `~/.gbrain/brain.pglite` (244MB, written daily). Staging data lake:
  `~/brains-ingest/` (markdown+JSON; X/fintwit 55 handles, YouTube 11 channels,
  Telegram scanners, Discord, captures, digests, reflections — written by this
  repo's recipes via `com.brains.feeds`).
- Old `com.brain.*` plists were retired to
  `~/Library/LaunchAgents/_retired-old-brain/` on 2026-07-01.
- Claude Code SessionStart/End hooks repointed 2026-07-02 →
  `~/.claude/hooks/brains-session-{start,end}.sh` → `infra/hooks/session.py` →
  capture sidecar. Old `brain-session-*.sh` hooks are orphaned (removable).
- A **curated legacy replay already ran 2026-07-02** via
  `recipes/legacy-replay/replay.py`: allow-list of 22 high-signal sources,
  skips `rss` + `forge_*` telemetry, idempotent (`source: legacy-<source>`),
  corpus 861 → 3,569 pages. Old DB holds ~22k episodes (~18.8k noisy rss).
- Sidecar `/api/*` requires `X-Brain-Key` (Keychain `brain`/`CAPTURE_KEY`);
  prefer the `gbrain` CLI for verification (no key needed).

## Advisability: self-hosted sandbox, reachable anywhere

Hosted agent runtimes (Hermes desktop, Railway, AlphaClaw — all tried, all
subscription-walled) also **cannot inherit Claude Max auth**: the flat-fee
quota is bound to the interactive `claude` CLI login on this machine, so any
cloud host converts reasoning into metered API spend on top of its own fee.
Self-hosting on the Mac is therefore not just cheaper — it is the only
architecture that preserves the $0 reasoning path.

Caveats:
1. **Keep inference on-host.** Containers/VMs on macOS lose Metal; Ollama and
   llama.cpp must not move into a sandbox VM. The effective sandbox already
   exists: dedicated `~/.gbrain` home, localhost-only binds, launchd user
   agents, and gbrain's fail-closed `OperationContext.remote` trust boundary
   (anything not strictly `false` is untrusted). Escalate to a separate macOS
   user or Lima VM only if remote agents will *execute code* here, not just
   query memory.
2. **Reachability:** Tailscale tailnet/Funnel (already proven for phone
   capture) + `gbrain serve --http` with remote tokens for external agents
   over MCP.
3. **Single-laptop constraint:** the brain sleeps when the Mac sleeps. Bridge
   with a work-hours `pmset`/caffeinate policy; an always-on box (M5 Pro
   handoff or a future mini) removes it.
4. **Secrets:** launchd cron cannot read the locked login Keychain after sleep
   (-25320); secret-bearing work stays inside the always-on KeepAlive sidecars.
   Corollary (fixed 2026-07-06): client hot paths must NEVER read the Keychain
   interactively — the 30s menubar poll, the per-session Claude hooks, and the
   capture server's per-request `_key()` each popped the macOS unlock dialog
   whenever the login keychain was locked. Pattern now: the capture sidecar
   resolves `CAPTURE_KEY` once at startup (env → `~/.gbrain/client.key` →
   Keychain), caches it in memory, and self-provisions the 0600
   `~/.gbrain/client.key` file; menubar + hooks read only that file.

## Migration plan (phased; each phase executable cold from this doc)

### Phase 0 — Finish archiving ~/brain
- Identify what wrote `~/brain/data/brain.db` on 2026-07-03 (likely backup job
  or the :8790 server touching WAL) and confirm zero writers remain.
- Mark `~/brain` archived in its README/CLAUDE.md; keep `~/brain-backups` and
  the :8790 read-only API.
- Resolve uncommitted `~/brain/infra/menubar/brains.30s.sh` (the gbrain
  repoint) — commit or discard.
- Harvest then delete the stalled worktrees: `~/brain-briefing`
  (feat/clarity-briefing), `~/brain-capture` (feat/capture-triage),
  `~/brain-clips` (feat/notion-clips-enrichment) — all idle since Jun 25–26.

### Phase 1 — Declare headless posture here
- Record in `FORK_PIN.md`: UI frozen; new capability lands only as
  skills/recipes/sidecars; core delta shrinks at each monthly rebase.
- Verify `gbrain config get search.mode` = `balanced`; set if not.
- `gbrain doctor --json` as the health baseline; commit the posture note.

### Phase 2 — Close the four ingest gaps (one additive recipe each)
Wire each into `com.brains.feeds` cron (or its own plist); dedup by stable
source_id/content hash, mirroring the old brain's cursor semantics. Order:
1. **`recipes/rss-to-brain/`** — the 7 curated finance feeds (list in
   `~/brain/config.toml` `[ingest.rss]`); dedup = sha(feed_url|entry_id) as in
   `~/brain/brain/ingest/rss.py`.
2. **`recipes/git-to-brain/`** — commit-walk the 5 configured repos + GitHub
   events; port cursor logic from `~/brain/brain/ingest/git.py` +
   `github.py` (cursor = last SHA / max event id).
3. **Voice inbox watcher** — extend the capture sidecar to watch
   `inbox/audio/` (BlackHole meeting recordings), reusing its existing
   transcription path; hash-dedup per `~/brain/brain/ingest/voice.py`.
   Phone voice capture already works; this adds Mac-side meeting audio.
4. **`recipes/email-to-brain/` triage** — heaviest, last: port
   classify→propose→approve (old `~/brain/brain/agents/triage.py`) onto the
   what-needs-me gate, reusing `~/brain/brain/integrations/gmail.py` /
   `outlook.py`. Triage frozen since 2026-05-06; needs OAuth re-auth.

### Phase 3 — Refresh/extend the curated legacy replay
The 2026-07-02 replay (22 sources) already landed. Remaining:
- Review the allow-list for gaps: voice transcripts, `user_model` beliefs,
  community summaries, top-salience episodes.
- Exclude `git_commit` episodes — Phase 2's git recipe re-walks full history
  natively.
- Re-run `recipes/legacy-replay/replay.py` (idempotent) for deltas, then
  `gbrain embed --stale`.

### Phase 4 — Skills rationalization vs Forge
This brain keeps memory-side skills only: capture, enrichment, retrieval,
briefing / fintwit-analyst / convergence / weekly-reflection. Anything that
*does work in repos* belongs to Forge. Salvage map for future Forge adoption
(reference only, not scheduled): from `~/brain` — gateway/proposals pattern
(`brain/agents/gateway.py`), retrieval-eval harness (`brain/eval/`),
zero-LLM typed relations (`brain/memory/typed_relations.py`), integrations
wrappers (`gmail.py`, `outlook.py`, `github.py`); from this fork — the
max-bridge, reranker, and capture sidecar patterns.

### Phase 5 — Reachability hardening
- **Found 2026-07-06: Tailscale is LOGGED OUT** (no serve config; phone capture
  over the tailnet/Funnel has been dead). Same failure class as the headless
  `claude` CLI logout that broke max-bridge — both credentials likely lapsed in
  the same migration/update. User-run to restore:
  1. `tailscale up` (browser login), then
  2. `tailscale serve --bg --https=443 http://127.0.0.1:8787`
  3. `tailscale funnel --bg 443` — only if phone capture must work OFF-tailnet
     (public URL; the API is X-Brain-Key-gated, capture-spam blast radius).
- **Headless `claude` CLI logged out** — run `claude` → `/login` to restore the
  max-bridge reasoning path (bridge now returns informative 503s until then).
- `gbrain serve --http` + remote tokens for external agents (trust boundary
  fail-closed by design) — set up when a remote agent actually needs it.
- Keep-awake policy (work-hours caffeinate or pmset schedule) — user decision.
- Keychain-bearing work remains in KeepAlive sidecars only; client hot paths
  read only the 0600 files under `~/.gbrain/` (client.key, discord_bot.token,
  discord_webhook.url; tradingview.token provisions itself once the secret is
  added to Keychain — it is currently unset, so the TradingView webhook accepts
  unauthenticated posts per its documented "when configured" contract: set one).

## Verification per phase
- **P0:** `lsof -iTCP:8790 -sTCP:LISTEN` still answers; `fs_usage`/log check
  shows no writer on `brain.db`; worktrees gone from `git -C ~/brain worktree list`.
- **P1:** `gbrain doctor --json` clean; `gbrain config get search.mode` → balanced.
- **P2 (each recipe):** run twice back-to-back → second run inserts 0 new
  pages (dedup holds); pages visible via `gbrain search`; feeds log clean.
- **P3:** replay re-run reports only deltas; `gbrain embed --stale` → 0 pending.
- **Hooks round-trip (any time):** start a Claude Code session in a configured
  repo → context injected; end it → session page exists in the corpus.
