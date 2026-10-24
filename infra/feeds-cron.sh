#!/bin/bash
# BRAINS daily feeds cron: collect Anthony's own work and notes → import →
# embed → snapshot. No third-party scraping and no digest since memory-only-v1
# (2026-10-24). Scheduled by infra/launchd/com.brains.feeds.plist.
export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
GB="$HOME/gbrain"
ING="$HOME/brains-ingest"
BV="$GB/sidecars/.venv/bin/python"        # self-contained: httpx + keyring
log() { echo "[$(date '+%F %T')] $*"; }

# Per-step network watchdog. 2026-07-08: youtube backfill hung 4+ days on an
# untimed transcript socket; launchd won't start a second com.brains.feeds
# while the label is alive, so EVERY feed went stale. Wrap each network recipe
# so one hung read can never wedge the whole pipeline again — TERM at the cap,
# KILL 30s later for any C-level blocked syscall. A timed-out step trips its own
# `|| log ... failed` fallback and the pipeline marches on. Override the ceiling
# with FEEDS_STEP_TIMEOUT=<seconds>.
STEP_TIMEOUT="${FEEDS_STEP_TIMEOUT:-1200}"   # 20 min per network step
if command -v timeout >/dev/null 2>&1; then RUN="timeout -k 30 $STEP_TIMEOUT"
elif command -v gtimeout >/dev/null 2>&1; then RUN="gtimeout -k 30 $STEP_TIMEOUT"
else RUN=""; log "warn: no timeout(1) on PATH — per-step watchdog disabled"; fi

# memory-only-v1 (2026-10-24, Anthony): Brains is memory only. The third-party
# scrapers (discord, youtube + backfill, x, rss, edgar, telegram), the weekly
# creator scorecard, the feeds watchdog and the daily digest + Discord post are
# retired here. Breakwise collects every market source itself
# (breakwise-standalone-v1) and posts "Breakwise Social Signals" (social-signals-v1).
# What stays: Anthony's own work and notes (git, prs, obsidian, captures, hermes).
log "collect: git (repo commits)"
$RUN "$BV" "$GB/recipes/git-to-brain/collect.py" "$ING" --max 200 || log "  git collect failed"
log "collect: prs (open pull requests)"
$RUN "$BV" "$GB/recipes/pr-to-brain/collect.py" "$ING" || log "  pr collect failed"
log "collect: obsidian (BrainVault)"
# Local-disk only (no network) but keep the watchdog for symmetry. Deliberately
# NOT in the health feed-freshness set: a quiet vault is normal, not stale.
$RUN "$BV" "$GB/recipes/obsidian-to-brain/collect.py" "$ING" || log "  obsidian collect failed"
log "collect: manual captures (iCloud BrainCapture/)"
"$BV" "$GB/recipes/manual-capture/import_captures.py" "$ING" || log "  capture import failed"
log "collect: hermes sessions (mirror ~/.hermes → brain; brain is canonical)"
# No-ops with a message until Hermes is installed (recipes/hermes-bridge/README.md).
"$BV" "$GB/recipes/hermes-to-brain/collect.py" || log "  hermes mirror failed"

log "import + embed"
gbrain import "$ING" --no-embed || log "  import failed"
gbrain embed --stale || log "  embed failed"

log "snapshot ingest corpus (git)"
# ~/brains-ingest is a git repo (initialized 2026-08-15) — one commit per night
# = corpus history for free. Local-only; must never fail the cron.
git -C "$ING" add -A && git -C "$ING" commit -q -m "daily snapshot $(date +%F)" || true

# Consolidation — the deep-sleep cycle is `gbrain dream` (facts · salience ·
# symbol-edges · consolidate · purge). It now lives in infra/dream-cron.sh
# (com.brains.dream.plist, 03:30 nightly), which GATES the dream on
# engine != pglite because of the 2026-07-02 trap: on PGLite it holds the
# single-writer lock for its whole multi-minute run (freezes the dashboard),
# and churn + a mid-run SIGTERM left ticker/catalyst pages soft-deleted (its
# purge phase then hard-deletes soft-deleted rows). The gate lifts automatically
# once the Postgres migration (task #43) lands; the cron is SIGTERM-safe (lets
# the running phase finish — never kill mid-transaction). Manual: `gbrain dream`.
log "done"
