# Fork pin

This is a fork of [garrytan/gbrain](https://github.com/garrytan/gbrain), used as
the base for a personal-memory rebuild ("Brains" features ported in). GBrain
churns fast, so we **pin to a commit SHA** and rebase onto a vetted SHA on a
schedule — never track `master` live. (Upstream now ships GitHub Releases and a
moving `latest-stable` tag — that answers the original "no usable release tags"
complaint and is the natural candidate for the next pin, but we still record
the SHA it resolves to, never the moving tag itself.)

## Pinned base
- **SHA:** `77bb9d8c` — `v0.46.32.0 fix: wave-k community train — 54 contributor PRs absorbed …`
- **Version:** 0.46.32.0
- **Pinned:** 2026-08-27
- **Why this SHA:** upstream `master` HEAD = the `latest-stable` tag at
  fork-sync time (the ref upstream's own BOOTSTRAP_FOR_AGENTS flow installs
  from). Vetted via typecheck + `bun run verify` (54/54) + unit suite +
  Postgres engine-parity E2E (47/47) + live doctor/models-doctor/import-dedup
  smokes before adoption.
- **Previous pin:** `814258dd` / v0.42.53.0 (2026-06-29).

## Upgrade notes from the 0.42.53 → 0.46.32 rebase (read before the next one)

- **`~/.bun/bin/gbrain` is a `bun link` of THIS working tree** — checking out a
  new base IS deploying it: the next cron tick (or `bun install` postinstall)
  runs the new code against the live brain and auto-applies schema migrations.
  Pause the `com.brains.*` timer jobs and take a `pg_dump` BEFORE the rebase
  checkout, not before "installing the binary" (there is no separate install).
- The live brain is **Postgres** (`postgresql://localhost/brain`) since
  2026-07-12 — backups are `infra/pg-backup.sh` (nightly 02:30) + manual runs,
  not PGLite file copies.
- `com.brains.mcp` is the only long-running process holding gbrain code in
  memory; `launchctl kickstart -k` it after any base change. Timer jobs load
  fresh code per fire.
- Model routing is config-plane, per tier (`models.tier.*`, `models.think`,
  `search.reranker.model`) — re-verify with `gbrain models list` +
  `gbrain models doctor` after upgrades; the 2026-06-29 A/B tiering
  (deep=max-bridge, reasoning=qwen3, utility=qwen2.5, reranker=local
  llama-server) is now persisted in DB config.
- Upstream test contracts fork-added files must satisfy: skills need
  `## Output Format` + `## Anti-Patterns` sections and an entry in either the
  plugin bundle or `skills/plugin-exclusions.json`; `skills.lock.json` and the
  committed `plugin/` + `plugin-variants/` trees are regenerated whenever a
  skill changes; recipe env names must appear in the three `.codex-plugin/mcp.json`
  env contracts; only Azure may override `resolveAuth`.
- Unknown CLI flags are hard errors since v0.42.76 — strict-flag-audit the
  cron scripts and skills (`--rerank` and `list -n` died in this range).

## Branch model
- `master` — mirrors `upstream/master` (fast-forward only; no local commits).
- `brains-port` — long-lived working branch off the pinned SHA. **All local work
  is additive** (new files; edits confined to registration points:
  `src/core/ai/recipes/index.ts`, `src/core/operations.ts`,
  `src/core/minions/handlers/index.ts`, the dream-phase list) so monthly rebases
  auto-merge.

## Posture (decided 2026-07-05 — headless convergence)
- **UI is frozen.** The v3 React dashboard (`dashboard/`) keeps serving its
  built assets off the capture sidecar, but no new UI work. New capability
  lands ONLY as skills/recipes/sidecars (thin harness, thick skills).
- **Shrink the core delta at every rebase** — anything that can move out of
  `src/` into a recipe/sidecar should.
- Decision record + migration plan:
  `docs/decisions/2026-07-05-headless-convergence.md`.

## Rebase policy
Monthly (or for a security fix): `git fetch upstream` → trial-branch
`git rebase upstream/master` onto a new vetted SHA → **regression gate** (E2E
green; Max-bridge round-trip; Discord recipe smoke; embedding dim-check = 1024;
`gbrain doctor` clean) → fast-forward `brains-port` only if green. Update this
file's pinned SHA + date on every successful rebase.

## Local sidecars (process-isolated, localhost-HTTP only)
- `sidecars/max-bridge/` — OpenAI-compatible server wrapping `claude-agent-sdk`
  (flat-fee Claude Max via the local `claude` CLI). Ports `~/brain/brain/llm/claude.py`.
- `sidecars/reranker/` — `llama-server --rerank` launcher (bge-reranker-v2-m3).
  Ports the `~/brain/brain/llm/rerank.py` contract.
- `migration/` — one-off helpers (NOT used for the fresh start; reserved for an
  optional filtered history replay later).
- `infra/launchd/` — `com.brain.*.plist` for the bridge, reranker, ollama.
