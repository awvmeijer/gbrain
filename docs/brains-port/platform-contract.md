# BRAINS platform contract — external agents, keys, tiers, provenance

*Sprint 0 (2026-07-01). How other agents (Claude Code, Forge, Hermes) connect to BRAINS as a shared memory substrate.*

## The surface

GBrain ships an HTTP MCP server with OAuth 2.1 (verified live this sprint):

```
gbrain serve --http --port <P> --enable-dcr [--public-url <https-url>] [--token-ttl <s>]
```

OAuth discovery (`GET /.well-known/oauth-authorization-server`) advertises:
- **endpoints:** `/authorize`, `/token`, `/register` (DCR), `/revoke`, `/mcp`, `/health`
- **grants:** `authorization_code` (+PKCE `S256`), `refresh_token`, `client_credentials`
- **token auth:** `client_secret_post`, `none` (public + PKCE)
- **scopes (the tier model, already built in):** `read`, `write`, `agent`, `sources_admin`, `users_admin`, `admin`

`/mcp` requires a bearer token (401 unauth — verified). An **admin token** is printed at startup for the `/admin` console.

## Tiers (map consumers → scopes)

| Consumer | Grant | Scopes | Why |
|---|---|---|---|
| **Claude Code** | *local CLI, no OAuth* | full (local scope-bypass) | Hooks shell `gbrain` directly on the brain host — fastest, no round-trip. |
| **Forge** | `client_credentials` (headless) | `read write` (bound `source_id=forge`) | Dual-writes typed, evidence-validated pages; reads the union for context. |
| **Hermes** | `authorization_code` + PKCE | `read write agent` | Desktop agent; reads memory, captures thoughts, **proposes** actions. |
| *(never granted remotely)* | — | `admin`, `sources_admin`, `users_admin` | Destructive/admin ops stay **localOnly**. |

**Actor attribution is automatic + unspoofable:** every `put_page` is server-stamped `source_kind: mcp:put_page` and lands in the client's bound `source_id`, so every page records *which consumer wrote it* without the client asserting identity.

**The localOnly boundary:** admin/user/source-management ops are unreachable over the network transport even with a token — only the loopback/CLI caller can invoke them. This is what makes it safe to expose `/mcp` over the Tailscale Funnel for Hermes (Sprint 9).

## ⚠️ PGLite single-writer — the daemon is coupled to Postgres

**Verified this sprint:** while `gbrain serve --http` holds the DB open, a concurrent `gbrain <cmd>` CLI call fails with `Timed out waiting for PGLite lock`. The capture sidecar (`:8787`) shells the CLI on every `/api/*` request, so **a permanent HTTP-MCP daemon cannot coexist with the sidecar on PGLite.**

**Decision:** the persistent daemon (`com.brains.mcp` launchd job) + the PGLite→Postgres migration are **one coupled step, done together just before Sprint 9** (Forge/Hermes are the only consumers that need the network daemon). Until then:
- **Claude Code (Sprint 7)** connects via the **local CLI** in its hooks — no daemon, no lock contention.
- Ad-hoc external access can start `serve --http` transiently when the sidecar is paused (`brains-ctl.sh pause`).

## Registering a consumer (once Postgres + the daemon are up)

```bash
# headless (Forge): client_credentials
gbrain auth register-client --name forge --grant client_credentials --scope "read write" --source-id forge
#   → prints client_id + client_secret (store in Keychain)

# interactive (Hermes): authorization_code + PKCE, public client
gbrain auth register-client --name hermes --grant authorization_code --scope "read write agent"
```

*(Exact `gbrain auth` subcommand flags to be confirmed against the CLI when the daemon step runs; the admin console `POST /admin/api/register-client` is the fallback.)*
