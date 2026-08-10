#!/bin/bash
# BRAINS daily feeds cron: collect all priority feeds → import → embed →
# synthesize a daily digest → post to Discord. Hands-off proactive digest,
# now on GBrain. Scheduled by infra/launchd/com.brains.feeds.plist.
export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
GB="$HOME/gbrain"
ING="$HOME/brains-ingest"
BV="$GB/sidecars/.venv/bin/python"        # self-contained: httpx + keyring
YV="$GB/recipes/youtube-to-brain/.venv/bin/python"
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

log "collect: discord"
$RUN "$BV" "$GB/recipes/discord-to-brain/collect.py" "$ING" || log "  discord collect failed"
log "collect: youtube"
$RUN "$YV" "$GB/recipes/youtube-to-brain/collect.py" "$ING" --limit 1 --summarize || log "  youtube collect failed"
log "collect: youtube history drip (backfill)"
$RUN "$YV" "$GB/recipes/youtube-to-brain/backfill.py" "$ING" --max-new 30 || log "  youtube backfill drip failed/timed out (will resume next night)"
log "collect: x (fintwit)"
$RUN "$BV" "$GB/recipes/x-to-brain/collect.py" "$ING" --per-handle-sleep 1.2 --max 25 || log "  x collect failed"
log "collect: rss (finance news)"
$RUN "$BV" "$GB/recipes/rss-to-brain/collect.py" "$ING" --max 50 || log "  rss collect failed"
log "collect: git (repo commits)"
$RUN "$BV" "$GB/recipes/git-to-brain/collect.py" "$ING" --max 200 || log "  git collect failed"
log "collect: obsidian (BrainVault)"
# Local-disk only (no network) but keep the watchdog for symmetry. Deliberately
# NOT in the health feed-freshness set: a quiet vault is normal, not stale.
$RUN "$BV" "$GB/recipes/obsidian-to-brain/collect.py" "$ING" || log "  obsidian collect failed"
log "collect: telegram (scanners)"
# Read-only over the official Telegram API; no-ops with a clear message until
# TELEGRAM_* secrets + session exist (recipes/telegram-to-brain/README.md).
$RUN "$BV" "$GB/recipes/telegram-to-brain/collect.py" "$ING" --limit 50 || log "  telegram collect failed (set up creds?)"
log "collect: manual captures (iCloud BrainCapture/)"
"$BV" "$GB/recipes/manual-capture/import_captures.py" "$ING" || log "  capture import failed"
log "collect: hermes sessions (mirror ~/.hermes → brain; brain is canonical)"
# No-ops with a message until Hermes is installed (recipes/hermes-bridge/README.md).
"$BV" "$GB/recipes/hermes-to-brain/collect.py" || log "  hermes mirror failed"

log "import + embed"
gbrain import "$ING" --no-embed || log "  import failed"
gbrain embed --stale || log "  embed failed"

log "synthesize daily digest"
# Deterministic context: recipes/fintwit-analyst/digest.py assembles ALL
# in-window source pages from disk and synthesizes via the Max bridge.
# (The old `gbrain think --since` path starved itself: ticker-list pages
# rank poorly against a thematic query, and prior digests self-poison the
# retrieval — see 2026-07-07 "no data" digest with fresh pages embedded.)
DIGEST="$("$BV" "$GB/recipes/fintwit-analyst/digest.py" "$ING" --days 2)"
if [ -z "$DIGEST" ]; then
  log "  deterministic digest failed; falling back to gbrain think"
  SINCE="$(date -v-2d +%F 2>/dev/null || date +%F)"
  DIGEST="$(gbrain think "Across my fintwit X feed, finance YouTube creators, Telegram scanner channels, and Discord from the last day: group by theme; name every ticker with the source's direction (bullish/bearish/watch) + any level or catalyst; treat the Telegram scanner ticker lists as a SCREEN (no direction), and LEAD with OVERLAPS — a scanner-flagged ticker that an X/YouTube source also has a directional take on; then what changed (new calls, reversals, conviction shifts); call out where sources conflict, both sides attributed; attribute every line to a source. Do NOT treat mention volume as a buy/sell signal. (skill: fintwit-analyst)" --since "$SINCE" 2>/dev/null | "$BV" "$GB/infra/clean_digest.py")"
fi

if [ -n "$DIGEST" ]; then
  # Persist as a page FIRST — the dashboard "Today" tab + the sidecar delivery both read it.
  DAY="$(date '+%F')"; DDIR="$ING/digests"; mkdir -p "$DDIR"
  printf -- "---\ntitle: Daily digest — %s\nsource: digest\ndate: %s\ntags: [digest, finance]\n---\n\n# Daily digest — %s\n\n%s\n" "$DAY" "$DAY" "$DAY" "$DIGEST" > "$DDIR/$DAY.md"
  # Deliver via the always-on sidecar (holds Keychain access); the short-lived cron
  # can't read a locked login keychain when the Mac slept through 06:30 (-25320).
  log "deliver digest to Discord (via sidecar)"
  if ! curl -sf -m 20 -X POST "http://127.0.0.1:8787/internal/post-digest" >/dev/null 2>&1; then
    log "  sidecar delivery failed; trying direct post"
    printf '**🧠 BRAINS daily digest — %s**\n\n%s\n' "$DAY" "$DIGEST" | "$BV" "$GB/infra/post_discord.py" || log "  direct post failed (keychain locked?)"
  fi
else
  log "no digest produced; skip post"
fi

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
