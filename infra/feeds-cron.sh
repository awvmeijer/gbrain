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

log "collect: discord"
"$BV" "$GB/recipes/discord-to-brain/collect.py" "$ING" || log "  discord collect failed"
log "collect: youtube"
"$YV" "$GB/recipes/youtube-to-brain/collect.py" "$ING" --limit 1 --summarize || log "  youtube collect failed"
log "collect: x (fintwit)"
"$BV" "$GB/recipes/x-to-brain/collect.py" "$ING" --per-handle-sleep 1.2 --max 25 || log "  x collect failed"
log "collect: telegram (scanners)"
# Read-only over the official Telegram API; no-ops with a clear message until
# TELEGRAM_* secrets + session exist (recipes/telegram-to-brain/README.md).
"$BV" "$GB/recipes/telegram-to-brain/collect.py" "$ING" --limit 50 || log "  telegram collect failed (set up creds?)"

log "import + embed"
gbrain import "$ING" --no-embed || log "  import failed"
gbrain embed --stale || log "  embed failed"

log "synthesize daily digest"
SINCE="$(date -v-2d +%F 2>/dev/null || date +%F)"
# Runs the fintwit-analyst skill's lens, scoped to ~2d, and cleans think's output
# (strips the echoed question + Model footer; renders any raw JSON).
DIGEST="$(gbrain think "Across my fintwit X feed, finance YouTube creators, Telegram scanner channels, and Discord from the last day: group by theme; name every ticker with the source's direction (bullish/bearish/watch) + any level or catalyst; LEAD with what changed (new calls, reversals, conviction shifts); call out where sources conflict, both sides attributed; attribute every line to a source. Do NOT treat mention volume as a buy/sell signal. (skill: fintwit-analyst)" --since "$SINCE" 2>/dev/null | "$BV" "$GB/infra/clean_digest.py")"

if [ -n "$DIGEST" ]; then
  log "post digest to Discord"
  printf '**🧠 BRAINS daily digest — %s**\n\n%s\n' "$(date '+%F')" "$DIGEST" | "$BV" "$GB/infra/post_discord.py"
else
  log "no digest produced; skip post"
fi
log "done"
