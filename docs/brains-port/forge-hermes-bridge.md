# Forge bridge + Hermes desktop — the external-agent seam (Sprint 9)

Status: **the local write/read seams exist and are live; the network daemon +
Hermes install are the two human-gated steps.** This doc is the contract.

## The picture

```
Forge (user's app/agent)  ──writes──▶  finance/watchlist        (source of truth for held/watched)
                          ──reads───▶  finding/* , /api/findings (the convergence output)

Hermes (Railway, cloud)   ──OAuth──▶   /mcp over Tailscale Funnel (read + write + propose)
                          ──proposes─▶  proposals-as-pages gate   (never acts directly)

Both share ONE brain on the M5. Memory never leaves the box.
```

## Forge bridge — LIVE today (no new code)

Forge is the source of truth for the watchlist and "detects all the data (as for
FCEL)". The write/read seams it needs already exist:

- **Write the watchlist** — `PUT /api/watchlist` with `{"held":[…],"watched":[…],"written_by":"forge"}`
  (or `POST /capture` with `written_by=forge`). Overwrites `finance/watchlist`; the
  EDGAR (`edgar-to-brain`), permit, federal (`federal-to-brain`), and convergence
  pollers all read that page. Auth = `X-Brain-Key` (the shared capture key) today;
  a scoped named key lands with the MCP daemon (below).
- **Read the findings** — `GET /api/findings` returns the ranked convergence board
  (`type: finding`, sorted by score then class-diversity). Forge surfaces the same
  finding the dashboard does.

So Forge → watchlist → pollers → convergence → finding → Forge is a **closed loop
right now** over the sidecar's JSON API. What's *pending* is only the OAuth-scoped,
actor-attributed version (below), which upgrades the shared-key write to a
`source_id=forge`, schema-validated write.

## Hermes — LIVE via HTTP MCP (2026-07-12); shim kept as fallback

**Update 2026-07-12 (Phase 2 landed):** the two gates below cleared. Engine is
Postgres (postgresql@17 + pgvector, parity verified — see
`docs/decisions/2026-07-12-postgres-migration-phase2.md`), the OAuth `/mcp`
daemon runs under `com.brains.mcp.plist` on `127.0.0.1:3131`, and
`~/.hermes/config.yaml` points `mcp_servers.brain` at it with
`auth: oauth` (pre-registered PKCE public client, scope `read write agent`,
redirect port 8971 — localhost, not the Funnel URL: Hermes is self-hosted on
the same Mac). Tool surface stays five tools, now gbrain's real op names
(`search`, `think`, `put_page`, `list_pages`, `get_page`); the stdio shim
block stays commented in config.yaml as fallback. One human step remains: the
first Hermes session opens the browser OAuth consent once, then tokens persist.

## Historical — LIVE via the shim (2026-07-11); MCP-proper still gated

**Update 2026-07-11:** Hermes now runs **self-hosted on this Mac** (not Railway —
hosted runtimes can't inherit Claude Max auth, see the 2026-07-05 decision doc),
with local Ollama `qwen3:30b-a3b` driving its tool loop and Claude flowing in
through `brain_ask` → the Max bridge. It reaches the brain through a stdio MCP
shim (`sidecars/hermes-shim/brain_mcp.py`) that translates tool calls into the
capture sidecar's JSON API — zero PGLite contention, so neither blocker below
gates the working integration. Setup, persona, and the Telegram approvals bot
live in `recipes/hermes-bridge/` + `recipes/telegram-approvals/` (the
`config/hermes/` referenced by earlier drafts never landed; this replaces it).
Hermes reads memory, captures thoughts (`source=hermes`), and **proposes**
actions through the proposals gate (`POST /api/propose`) — never acts directly.

The OAuth-scoped `/mcp` daemon remains the *upgrade path*, still gated on two
human steps:

1. **The MCP daemon needs Postgres.** `gbrain serve --http --enable-dcr` holds the
   PGLite lock, which single-writer-conflicts with the CLI-shelling sidecar
   (verified in Sprint 0). So the always-on `/mcp` listener is coupled to the
   Postgres migration (task #43): `brew install postgresql` → `gbrain migrate` →
   re-verify dim=1024 + `gbrain stats` identical → load `com.brains.mcp`.
2. **Hermes install.** Install Hermes, complete its Add-MCP-Server flow pointed at
   the Funnel URL (`https://anthonys-macbook-pro.<tailnet>.ts.net/mcp`), OAuth
   `authorization_code + PKCE`, scope read+write+propose.

## Verification (when the two gates clear)

- Forge client writes `finance/watchlist` (`written_by=forge`) → the EDGAR/federal
  pollers consume it on the next run (already true over the shared key today).
- Hermes: "what do I know about FCEL" cites brain pages; a Hermes-proposed action
  lands in `/api/needs`; a `localOnly` op is unreachable over the Funnel; revoking
  the token cuts access.

## What shipped autonomously this sprint

Nothing *new* was required for the Forge side — Sprint 2's `/api/watchlist` +
Sprint 4's `/api/findings` already are the Forge bridge. This doc freezes the
contract and records the two remaining human gates (Postgres daemon, Hermes
install) so the rollout is mechanical.
