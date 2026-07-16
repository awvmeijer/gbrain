# 2026-07-12 — Postgres migration + OAuth MCP daemon (Hermes Phase 2)

Phase 2 of the Hermes-on-gbrain plan
(`~/.claude/plans/what-is-left-for-swirling-sutherland.md`, approved 2026-07-11).

## What changed

- **Engine: PGLite → Postgres.** `postgresql@17` (17.10, Homebrew service) +
  pgvector 0.8.5, database `brain` on localhost:5432.
  `gbrain migrate --to supabase --url postgresql://localhost/brain` transferred
  all 4281 pages; schema bootstrapped to v119.
- **Parity verified.** Pages 4281=4281, tags 127, timeline 177, embeddings 100%
  at dim 1024, `gbrain doctor` clean of migration issues. Chunks 5506→5495 and
  links 2651→2640 are each −11: exactly the 11 soft-deleted pages' chunks and
  the 11 links touching them, which `migrate-engine` correctly excludes
  (stats counts all chunks; `listPages` hides soft-deleted). Confirmed by
  querying the retired PGLite directly.
- **Single-writer lock is gone.** Sidecar (:8787), MCP daemon (:3131), and
  manual CLI verified running concurrently. The dream gate in
  `infra/dream-cron.sh` (engine != pglite) lifts automatically.
- **OAuth MCP daemon**: `infra/launchd/com.brains.mcp.plist` runs
  `gbrain serve --http --enable-dcr --port 3131 --bind 127.0.0.1`
  (v0.42.53.0). Verified: unauthenticated /mcp → 401; client_credentials
  token → tools/list works; `gbrain auth revoke-client` kills the token
  (401); /admin APIs 401 without the admin token.
- **Hermes OAuth client** (pre-registered, PKCE-only public client):
  client_id `gbrain_cl_bc5a95b0…6f158b`, grants
  `authorization_code,refresh_token`, scopes `read write agent`, redirect
  `http://127.0.0.1:8971/callback`, token auth method `none`.
- **Hermes switched to HTTP MCP.** `~/.hermes/config.yaml` `mcp_servers.brain`
  now points at `http://127.0.0.1:3131/mcp` with `auth: oauth`
  (`redirect_port: 8971` fixed to match the registered URI); the Phase-1 stdio
  shim block is kept commented as fallback. Tool surface stays 5 tools, now
  gbrain's real op names: `search`, `think`, `put_page`, `list_pages`,
  `get_page` (Hermes prefixes → `mcp_brain_*`). Persona
  (`recipes/hermes-bridge/persona.md` → `~/.hermes/SOUL.md`) rewritten for the
  new names; propose-don't-act contract unchanged (proposals are `put_page`
  with `type: proposal, status: pending` frontmatter; deciding stays human).
- **Backups: pg_dump replaces pglite-file copies.**
  `infra/pg-backup.sh` + `infra/launchd/com.brains.pg-backup.plist` — nightly
  02:30 custom-format dump to `~/brains-backups/pg/`, 14-day rotation. First
  dump verified restorable (`pg_restore --list`). The pre-migration PGLite
  snapshot lives at `~/brains-backups/pre-postgres-2026-07-12/`; the original
  `~/.gbrain/brain.pglite` is retired in place (do not delete until a few
  nightly dumps have accumulated).

## Remaining human step

First Hermes session after this change triggers the one-time OAuth
handshake: browser opens gbrain's consent page, callback on
127.0.0.1:8971. Tokens then persist in `~/.hermes` storage. The admin
bootstrap token (for /admin) prints in `logs/mcp-svc.log` on daemon start.

## Rollback

- Hermes → shim: restore the commented block in `~/.hermes/config.yaml`
  (backup: `config.yaml.bak.pre-phase2-*`; persona backup
  `SOUL.md.bak.pre-phase2`).
- Engine → PGLite: `gbrain migrate --to pglite` (the retired
  `~/.gbrain/brain.pglite` also still holds the pre-migration state), then
  `launchctl bootout gui/$UID/com.brains.mcp` — the MCP daemon must not run
  on PGLite (single-writer lock).
