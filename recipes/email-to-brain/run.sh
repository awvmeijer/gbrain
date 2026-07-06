#!/bin/bash
# BRAINS email-triage cron: classify unread Gmail → proposal pages → import.
# Scheduled by infra/launchd/com.brains.email-triage.plist (4x/day, waking
# hours — mirrors the old com.brain.triage cadence). Proposals surface in
# what-needs-me / the dashboard's /api/needs; approval + execution are
# separate explicit steps (proposals-as-pages gate).
export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
GB="$HOME/gbrain"
ING="$HOME/brains-ingest"
# Recipe-local venv (google-api-python-client isn't in sidecars/.venv) —
# see README "Install" for how to create it.
EV="$GB/recipes/email-to-brain/.venv/bin/python"
log() { echo "[$(date '+%F %T')] $*"; }

if [ ! -x "$EV" ]; then
  log "email-triage: no recipe venv at $EV — see recipes/email-to-brain/README.md (Install)"
  exit 0
fi

log "collect: email triage (gmail → proposal pages)"
"$EV" "$GB/recipes/email-to-brain/triage.py" "$ING" || { log "  triage failed (auth stale? bridge down?)"; exit 0; }

log "import proposals"
gbrain import "$ING" --no-embed || log "  import failed"
gbrain embed --stale || log "  embed failed"
log "done"
