#!/bin/bash
# BRAINS nightly dream cron: run the deep-sleep cycle (`gbrain dream` — facts ·
# salience · symbol-edges · consolidate · purge), then the LLM typed-relations
# pass. Scheduled by infra/launchd/com.brains.dream.plist at 03:30.
#
# WHY THIS WAS DISABLED (and what makes it safe now):
#   During 2026-07-02 testing, `gbrain dream` on PGLite (1) held the single-
#   writer lock for its whole multi-minute run, freezing the dashboard, and
#   (2) a mid-run SIGTERM landed between phases and the purge phase then
#   hard-deleted pages that were only soft-deleted — real data loss (see the
#   comment block at the end of infra/feeds-cron.sh, and task #43).
#   Mitigations here:
#     - ENGINE GATE: dream only runs when the engine is NOT pglite (i.e. the
#       Postgres migration has landed and the single-writer lock is gone).
#       Until then this script logs and skips straight to the relations pass,
#       so the plist can stay loaded and the cron self-activates post-migration.
#     - SIGTERM-SAFE: launchd sends SIGTERM on unload/shutdown. We trap it and
#       let the running dream finish instead of killing it mid-transaction;
#       the plist's generous ExitTimeOut gives launchd patience before SIGKILL.
#     - LOCK CHECK: skip if another cycle holds ~/.gbrain/cycle.lock (live PID).
#     - caffeinate keeps the Mac from idle-sleeping while we run (pair with
#       `sudo pmset repeat wakeorpoweron MTWRFSU 03:25:00` so it wakes at all).
export PATH="$HOME/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
GB="$HOME/gbrain"
BV="$GB/sidecars/.venv/bin/python"
LOCK="$HOME/.gbrain/cycle.lock"
log() { echo "[$(date '+%F %T')] $*"; }

# Keep the system awake for the lifetime of this script (dream + relations).
caffeinate -s -w $$ &

ENGINE="$(python3 -c 'import json,pathlib;print(json.load(open(pathlib.Path.home()/".gbrain"/"config.json")).get("engine","pglite"))' 2>/dev/null || echo pglite)"

if [ "$ENGINE" = "pglite" ]; then
  log "engine=pglite — dream stays DISABLED (2026-07-02 data-loss class: mid-run kill → purge hard-deletes; single-writer lock starves the sidecar). Runs automatically once the Postgres migration lands."
else
  # Lock check: another cycle already running? gbrain's own cycle lock is
  # authoritative (PID-liveness); this just avoids queueing a doomed run.
  RUNNING=0
  if [ -f "$LOCK" ]; then
    PID="$(head -1 "$LOCK" 2>/dev/null)"
    case "$PID" in (*[!0-9]*|"") ;; (*) kill -0 "$PID" 2>/dev/null && RUNNING=1 ;; esac
  fi
  if [ "$RUNNING" = 1 ]; then
    log "cycle.lock held by live pid $PID — skipping tonight's dream"
  else
    log "dream: start (engine=$ENGINE)"
    gbrain dream &
    DREAM_PID=$!
    # SIGTERM-safe: never forward the signal — let the running phase finish.
    trap 'log "SIGTERM received — letting the running dream phase finish (never kill mid-transaction)"' TERM
    RC=1
    while kill -0 "$DREAM_PID" 2>/dev/null; do
      wait "$DREAM_PID"; RC=$?
    done
    trap - TERM
    log "dream: done rc=$RC"
  fi
fi

# Typed entity relations (LLM, local qwen3 via Ollama) — runs after dream so
# it sees tonight's consolidated corpus; safe on PGLite too (short-lived
# `gbrain link` invocations, same contention profile as feeds-cron's import).
# 2026-08-10: limit 40 → 200, --days 2 → 30. At 40/night (~73s of qwen) the
# 6,024-page orphan backlog never clears; 200/night is still <10 min on an
# idle Mac at 03:30, and the wider window lets the pass eat into history
# instead of only skimming the last two days.
log "typed relations: LLM pass over recent pages"
"$BV" "$GB/recipes/entity-extract/llm_relations.py" --days 30 --limit 200 || log "  relations pass failed"

log "done"
