# Fork pin

This is a fork of [garrytan/gbrain](https://github.com/garrytan/gbrain), used as
the base for a personal-memory rebuild ("Brains" features ported in). GBrain
churns fast (~14 version bumps in 10 days, no usable release tags), so we **pin
to a commit SHA** and rebase onto a vetted SHA on a schedule — never track
`master` live.

## Pinned base
- **SHA:** `814258dd` — `v0.42.53.0 fix(sync,db): #2339 op_checkpoints …`
- **Version:** 0.42.53.0
- **Pinned:** 2026-06-29
- **Why this SHA:** newest upstream `master` HEAD at fork-sync time; a version-tagged
  release commit at the end of a `fix(sync,db)` cluster (the most-firefought
  subsystem), per the migration plan's pin-selection gate. Vetted via
  `gbrain doctor` + bundled E2E before committing work on top.

## Branch model
- `master` — mirrors `upstream/master` (fast-forward only; no local commits).
- `brains-port` — long-lived working branch off the pinned SHA. **All local work
  is additive** (new files; edits confined to registration points:
  `src/core/ai/recipes/index.ts`, `src/core/operations.ts`,
  `src/core/minions/handlers/index.ts`, the dream-phase list) so monthly rebases
  auto-merge.

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
