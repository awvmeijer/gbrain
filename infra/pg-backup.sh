#!/bin/bash
# Nightly Postgres backup for the brain DB (Phase 2 of the Hermes bridge).
# pg_dump custom-format → ~/brains-backups/pg/brain-YYYY-MM-DD.dump, then
# rotate: keep 14 days. Replaces the pglite-file backup story (the pre-migration
# PGLite snapshot lives in ~/brains-backups/pre-postgres-2026-07-12/).
# Scheduled by infra/launchd/com.brains.pg-backup.plist (02:30, before dream).
set -euo pipefail
export PATH="/opt/homebrew/opt/postgresql@17/bin:/opt/homebrew/bin:/usr/bin:/bin"

DEST="$HOME/brains-backups/pg"
DAY="$(date '+%F')"
OUT="$DEST/brain-$DAY.dump"
mkdir -p "$DEST"

log() { echo "[$(date '+%F %T')] $*"; }

log "pg_dump brain → $OUT"
pg_dump --format=custom --file="$OUT.tmp" brain
mv "$OUT.tmp" "$OUT"

# Sanity: a dump under 1MB of a ~4K-page brain means something went wrong.
SIZE=$(stat -f%z "$OUT")
if [ "$SIZE" -lt 1048576 ]; then
  log "WARN: dump suspiciously small ($SIZE bytes) — inspect before trusting"
fi
log "dump ok ($SIZE bytes)"

# Rotate: delete dumps older than 14 days.
find "$DEST" -name 'brain-*.dump' -mtime +14 -print -delete | while read -r f; do
  log "rotated out: $f"
done
log "done"
