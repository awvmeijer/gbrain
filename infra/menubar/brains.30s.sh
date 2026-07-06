#!/bin/bash
# <xbar.title>BRAINS status</xbar.title>
# <xbar.desc>GBrain health in the menubar — real feed freshness + sidecar liveness.</xbar.desc>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
#
# GBrain-era menubar plugin. Reads the capture sidecar's COMPOSITE /api/health
# (feed freshness + ollama/bridge/reranker liveness) — NOT the bare /health,
# which returns {"ok":true} unconditionally and caused the old false-green.
#
# This directory IS the SwiftBar plugin folder (com.ameba.SwiftBar
# PluginDirectory points here) — edits go live on the next refresh; keep this
# the ONLY copy. The 30s in the filename sets the refresh interval.
#
# States: 🟢/🟡/🔴 verdict · ⏸ paused (brains-ctl freed RAM) · 🔑 auth failed.

URL="http://127.0.0.1:8787"
CTL="$HOME/gbrain/infra/brains-ctl.sh"
LOGS="$HOME/gbrain/logs"
U=$(id -u)

# Key file, NOT keyring — a Keychain read every 30s pops the macOS unlock
# dialog whenever the login keychain is locked. The capture sidecar
# self-provisions ~/.gbrain/client.key at startup; kickstart it to re-provision.
KEY=$(cat "$HOME/.gbrain/client.key" 2>/dev/null)

# Paused = ALL heavy jobs booted out (brains-ctl pause signature). A partial
# outage (e.g. only ollama down) is NOT paused and must stay an alarm state.
PAUSED=1
for j in com.brain.ollama com.brains.reranker com.brains.max-bridge; do
  launchctl print "gui/$U/$j" >/dev/null 2>&1 && { PAUSED=0; break; }
done

H=$(curl -s --max-time 6 -H "X-Brain-Key: $KEY" "$URL/api/health" 2>/dev/null)

restart_item() {
  echo "Restart capture sidecar | bash=/bin/launchctl param1=kickstart param2=-k param3=gui/$U/com.brains.capture terminal=false refresh=true"
}

if [ -z "$H" ]; then
  echo "🔴 brains"
  echo "---"
  if [ -z "$KEY" ]; then
    echo "~/.gbrain/client.key missing — sidecar re-provisions it on start | color=orange"
  else
    echo "Capture sidecar not responding | color=orange"
  fi
  restart_item
  echo "Refresh | refresh=true"
  exit 0
fi

# A 401 body has no "verdict" — auth failure is not the same as "down".
if ! printf '%s' "$H" | grep -q '"verdict"'; then
  echo "🔑 brains"
  echo "---"
  echo "Auth failed — ~/.gbrain/client.key stale or missing | color=orange"
  echo "Restarting the sidecar re-provisions the key file | size=11"
  restart_item
  echo "Refresh | refresh=true"
  exit 0
fi

read -r VERDICT OLL BR RER STALE < <(
  printf '%s' "$H" | python3 -c 'import json,sys
d=json.load(sys.stdin); s=d.get("services",{})
stale=[f["source"] for f in d.get("feeds",[]) if f.get("stale")]
print(d.get("verdict"), s.get("ollama"), s.get("bridge"), s.get("reranker"), ",".join(stale) or "-")' 2>/dev/null
)

if [ "$PAUSED" = 1 ]; then
  ICON="⏸"
else
  case "$VERDICT" in
    ok)   ICON="🟢";;
    warn) ICON="🟡";;
    *)    ICON="🔴";;
  esac
fi

echo "$ICON brains"
echo "---"
if [ "$PAUSED" = 1 ]; then
  echo "Paused — heavy sidecars unloaded, RAM freed (capture stays up)"
else
  echo "Verdict: ${VERDICT:-?}"
  echo "Ollama: ${OLL} · Bridge: ${BR} · Reranker: ${RER}"
  [ "$STALE" != "-" ] && echo "Stale feeds: $STALE | color=orange"
fi
echo "---"
echo "Feed freshness | size=11"
printf '%s' "$H" | python3 -c 'import json,sys
for f in json.load(sys.stdin).get("feeds",[]):
    a=f.get("age_hours"); m="!" if f.get("stale") else "·"
    age=(str(a)+"h") if a is not None else "—"
    print(m+" "+f["source"]+": "+age)' 2>/dev/null
echo "---"
echo "Open dashboard | href=$URL/"
if [ "$PAUSED" = 1 ]; then
  echo "Resume | bash=\"$CTL\" param1=resume terminal=false refresh=true"
else
  echo "Pause (free RAM) | bash=\"$CTL\" param1=pause terminal=false refresh=true"
  echo "Ingest feeds now | bash=/bin/launchctl param1=kickstart param2=gui/$U/com.brains.feeds terminal=false refresh=true"
fi
echo "Open logs folder | bash=/usr/bin/open param1=$LOGS terminal=false"
echo "Refresh | refresh=true"
