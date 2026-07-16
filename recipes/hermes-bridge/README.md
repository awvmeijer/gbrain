# hermes-bridge — Hermes Agent on the brain, reachable from the phone

Self-hosted [Hermes Agent](https://github.com/NousResearch/hermes-agent)
(installed at `~/.hermes`, pinned v0.18.0) using **the brain as canonical
long-term memory** and **Telegram as the phone surface**. $0 marginal
reasoning: local Ollama `qwen3:30b-a3b` drives the tool-calling agent loop;
Claude-quality synthesis flows in through `brain_ask` → capture sidecar →
`gbrain think` → the Max bridge (:8789, flat-fee). Design contract:
`docs/brains-port/forge-hermes-bridge.md`.

## Architecture

```
phone (Telegram) ──long-poll──► hermes gateway ─► agent loop (ollama qwen3:30b-a3b)
                                                     │  MCP stdio
                                    sidecars/hermes-shim/brain_mcp.py
                                                     │  HTTP + X-Brain-Key
                                    capture sidecar :8787 ──shells──► gbrain (PGLite)
                                                     │
                              brain_ask → gbrain think → max-bridge :8789 (Claude Max)

phone (Telegram) ──long-poll──► recipes/telegram-approvals/bot.py ─► /api/needs + /api/decide
```

Why a shim and not `gbrain serve`: the MCP daemon holds the PGLite write lock
for the whole session and starves the sidecar. The shim keeps the sidecar the
sole gbrain invoker. After the Postgres migration (task #43), switch
`mcp_servers.brain` to the OAuth HTTP daemon (`gbrain serve --http --enable-dcr`)
and retire the shim.

## Safety contract (four layers)

1. **Shim surface** — only `brain_search / brain_ask / brain_capture /
   brain_needs(read) / brain_propose`. No decide, no raw put, no send.
2. **Hermes config** — `mcp_servers.brain.tools.include` pins that list;
   resources/prompts disabled.
3. **Persona** (`persona.md` → `~/.hermes/SOUL.md`) — propose-don't-act,
   capture salient facts, cite slugs.
4. **Approvals path** — deciding happens only in the dashboard or the
   dedicated approvals bot (deterministic, LLM-free, user-ID allowlisted);
   `POST /api/decide` stamps `decided_by`.

Proposals follow `skills/conventions/proposals.md` via the sidecar's
`POST /api/propose` (five required fields, else 400).

## Memory: brain is canonical

- Real-time: the persona instructs `brain_capture` at the moment something
  salient happens (lands as a `source=hermes` capture page).
- Safety net: `recipes/hermes-to-brain/collect.py` (in `infra/feeds-cron.sh`)
  mirrors Hermes session transcripts + SOUL/MEMORY/USER snapshots into
  `brains-ingest/hermes/` nightly. Debug `request_dump_*` files are skipped.

## Setup

```bash
# 1. Two bots via @BotFather (one for Hermes chat, one for approvals);
#    your numeric id via @userinfobot. Store once in Keychain:
security add-generic-password -U -s brain -a TELEGRAM_HERMES_BOT_TOKEN    -w '<token1>'
security add-generic-password -U -s brain -a TELEGRAM_APPROVALS_BOT_TOKEN -w '<token2>'
security add-generic-password -U -s brain -a TELEGRAM_ALLOWED_USER_ID     -w '<numeric id>'

# 2. Wire everything (idempotent; backs up config.yaml + SOUL.md):
recipes/hermes-bridge/setup.sh

# 3. Services:
hermes gateway install && hermes gateway start        # Hermes's native launchd unit
cp infra/launchd/com.brains.telegram-approvals.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.brains.telegram-approvals.plist
```

Two bot tokens because Telegram allows one `getUpdates` consumer per token —
and because approvals must never route through an LLM loop.

Everything is outbound long-polling: **no Tailscale funnel required** for the
phone path. (Funnel only matters for the capture PWA / future remote MCP.)

## Verify

- Phone → Hermes bot: "what does my brain know about FCEL" → answer cites
  slugs (verified 2026-07-11 via `hermes -z`, qwen3 loop + shim).
- `brain_ask` path: cited synthesis via the Max bridge (verified 2026-07-11).
- Propose→decide: `POST /api/propose` → appears in `/api/needs` → approve on
  dashboard or bot → `status: approved`, `decided_by:` stamped (verified).
- Approvals bot: `/needs` lists buttons; a non-allowlisted user is refused.
- RAM: qwen3 resident ≈20 GB; keep `gemma4:26b` on-demand only (64 GB box).

## Model switching

`hermes model qwen-local` (default, free) · `hermes model bridge` (Claude via
max-bridge — TEXT-ONLY, tool calls don't work there; use for pure chat).
