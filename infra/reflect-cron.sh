#!/bin/bash
# BRAINS weekly reflection — runs the weekly-reflection skill's lens, writes a
# reflections/<iso-week> page, and posts a summary to Discord. Scheduled by
# infra/launchd/com.brains.reflect.plist (Sunday 18:00).
export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
GB="$HOME/gbrain"
BV="$GB/sidecars/.venv/bin/python"
WEEK="$(date +%Y-W%V)"
log() { echo "[$(date '+%F %T')] $*"; }

log "reflecting on week $WEEK"
SINCE="$(date -v-8d +%F 2>/dev/null || date +%F)"
OUT="$(gbrain think "Reflect on the brain's last 7 days across my fintwit X, YouTube, and Discord feeds. Write a WEEK-LEVEL reflection, not a re-digest: (1) recurring themes/tickers and how attention shifted day to day; (2) conviction changes by source (who moved watch->conviction or reversed); (3) calls that visibly played out or broke this week; (4) what I engaged with or flagged; (5) what to watch next week. Attribute every point to a source page. Be honest if the week was thin. (skill: weekly-reflection)" --since "$SINCE" 2>/dev/null | "$BV" "$GB/infra/clean_digest.py")"

if [ -z "$OUT" ]; then
  log "no reflection produced; skipping"
  exit 0
fi

log "writing page reflections/$WEEK"
RDIR="$HOME/brains-ingest/reflections"; mkdir -p "$RDIR"
printf -- "---\ntype: reflection\nweek: %s\ncreated: %s\ntags: [reflection, finance]\n---\n\n# Weekly reflection — %s\n\n%s\n" \
  "$WEEK" "$(date +%F)" "$WEEK" "$OUT" > "$RDIR/$WEEK.md"
gbrain import "$HOME/brains-ingest" --no-embed >/dev/null 2>&1 && gbrain embed --stale >/dev/null 2>&1

log "posting summary to Discord"
printf '**🧠 Weekly reflection — %s**\n\n%s\n' "$WEEK" "$OUT" | "$BV" "$GB/infra/post_discord.py"
log "done"
