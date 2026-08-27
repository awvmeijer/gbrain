#!/bin/bash
# <xbar.title>FORGE status</xbar.title>
# <xbar.desc>Forge substrate health in the menubar — real per-source freshness, not liveness.</xbar.desc>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
#
# Sibling of brains.30s.sh, and it exists for the same reason that one does.
# Forge's /api/health returned {"status":"ok"} unconditionally until
# 2026-08-18, which is how every price in the system read null for a full day
# without anything noticing. It now carries a composite `verdict` computed
# from per-source freshness, in the same shape gbrain's sidecar uses, so this
# plugin is close to a copy of the one next to it.
#
# States: 🟢 ok · 🟡 warn (something stale or never run) · 🔴 fail (a source
# has gone dark) · ⚫️ substrate not responding.
#
# This directory IS the SwiftBar plugin folder (com.ameba.SwiftBar
# PluginDirectory points here) — edits go live on the next refresh; keep this
# the ONLY copy. The 60s in the filename sets the refresh interval.

URL="http://127.0.0.1:8800"
WEB="http://127.0.0.1:3000"
FORGE_DIR="$HOME/Documents/Claude Code/forge/forge2"
U=$(id -u)

H=$(curl -s --max-time 6 "$URL/api/health" 2>/dev/null)

start_item() {
  # The launchd unit is committed but TCC has to allow it access to
  # ~/Documents first; until then the substrate runs detached.
  if launchctl print "gui/$U/com.forge.substrate" >/dev/null 2>&1; then
    echo "Restart substrate | bash=/bin/launchctl param1=kickstart param2=-k param3=gui/$U/com.forge.substrate terminal=false refresh=true"
  else
    echo "Start substrate (detached) | bash=\"$FORGE_DIR/scripts/dev.sh\" terminal=true refresh=true"
    echo "launchd unit not loaded — TCC blocks ~/Documents | size=11 color=orange"
  fi
}

if [ -z "$H" ]; then
  echo "⚫️ forge"
  echo "---"
  echo "Substrate not responding on :8800 | color=orange"
  start_item
  echo "Refresh | refresh=true"
  exit 0
fi

# No verdict field means an OLD substrate is running — the version that always
# said ok. Report that honestly rather than inheriting its false green.
if ! printf '%s' "$H" | grep -q '"verdict"'; then
  echo "🟡 forge"
  echo "---"
  echo "Substrate predates the freshness verdict | color=orange"
  echo "Its /api/health always answers ok — restart to pick up the new build | size=11"
  start_item
  echo "Refresh | refresh=true"
  exit 0
fi

read -r VERDICT BROKEN TOTAL SCHED WAL < <(
  printf '%s' "$H" | python3 -c 'import json,sys
d=json.load(sys.stdin); f=d.get("freshness",{})
s=f.get("sources",[])
print(d.get("verdict","?"), sum(1 for x in s if x.get("stale")), len(s),
      f.get("scheduler","?"), f.get("wal_bytes",0))' 2>/dev/null
)

case "$VERDICT" in
  ok)   ICON="🟢";;
  warn) ICON="🟡";;
  *)    ICON="🔴";;
esac

echo "$ICON forge"
echo "---"
if [ "$BROKEN" = "0" ]; then
  echo "All $TOTAL sources inside their expected window"
else
  echo "$BROKEN of $TOTAL sources need attention | color=orange"
fi
[ "$SCHED" != "on" ] && echo "Scheduler is OFF — nothing refreshes on its own | color=orange"
# 50MB of un-checkpointed WAL means a backup of the .duckdb alone would
# capture almost nothing. The hourly checkpoint job should keep this small.
[ "${WAL:-0}" -gt 52428800 ] 2>/dev/null && echo "$((WAL / 1048576)) MB un-checkpointed WAL | color=orange"

echo "---"
echo "Sources | size=11"
printf '%s' "$H" | python3 -c '
import json, sys
MARK = {"fresh": "\u00b7", "off": "\u2013", "stale": "!", "dark": "\u2715", "never": "?"}
for s in json.load(sys.stdin).get("freshness", {}).get("sources", []):
    a = s.get("age_hours")
    if a is None:
        age = "never"
    elif a >= 1:
        age = "%.0fh" % a
    else:
        age = "%.0fm" % (a * 60)
    line = "%s %s: %s" % (MARK.get(s.get("state"), "?"), s.get("label"), age)
    print(line + (" | color=orange" if s.get("stale") else " | color=#6f7479"))
' 2>/dev/null

echo "---"
# The cockpit is a separate process from the substrate and dies independently.
# A menu item that opens a dead page is worse than one that says the page is
# dead, so check before offering the link.
if curl -s -o /dev/null --max-time 2 "$WEB"; then
  echo "Open cockpit | href=$WEB/data"
else
  echo "Cockpit not running on :3000 | color=orange"
  echo "Start cockpit | bash=/bin/sh param1=-c param2=\"cd '$FORGE_DIR/shell' && npx next dev\" terminal=true refresh=true"
fi
echo "Open freshness API | href=$URL/api/freshness"
start_item
echo "Refresh | refresh=true"
