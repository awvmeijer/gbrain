#!/bin/bash
# <xbar.title>BRAINS status</xbar.title>
# <xbar.desc>GBrain health in the menubar — real feed freshness + sidecar liveness.</xbar.desc>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
#
# GBrain-era menubar plugin. Reads the capture sidecar's COMPOSITE /api/health
# (feed freshness + ollama/bridge/reranker liveness) — NOT the bare /health,
# which returns {"ok":true} unconditionally and caused the old false-green.
#
# Install:
#   mkdir -p ~/.swiftbar && cp infra/menubar/brains.30s.sh ~/.swiftbar/ && chmod +x ~/.swiftbar/brains.30s.sh
#   open SwiftBar → set the plugin folder to ~/.swiftbar
# The 30s in the filename sets the refresh interval.

URL="http://127.0.0.1:8787"
CTL="$HOME/gbrain/infra/brains-ctl.sh"
VPY="$HOME/gbrain/sidecars/.venv/bin/python"
KEY=$("$VPY" -m keyring get brain CAPTURE_KEY 2>/dev/null)
H=$(curl -s --max-time 6 -H "X-Brain-Key: $KEY" "$URL/api/health" 2>/dev/null)

if [ -z "$H" ]; then
  echo "🔴 brains"
  echo "---"
  echo "Capture service not responding (down or key missing)"
  echo "Refresh | refresh=true"
  exit 0
fi

read -r VERDICT OLL BR RER STALE < <(
  echo "$H" | python3 -c 'import json,sys
d=json.load(sys.stdin); s=d.get("services",{})
stale=[f["source"] for f in d.get("feeds",[]) if f.get("stale")]
print(d.get("verdict"), s.get("ollama"), s.get("bridge"), s.get("reranker"), ",".join(stale) or "-")' 2>/dev/null
)

case "$VERDICT" in
  ok)   ICON="🟢";;
  warn) ICON="🟡";;
  *)    ICON="🔴";;
esac

echo "$ICON brains"
echo "---"
echo "Verdict: ${VERDICT:-?}"
echo "Ollama: ${OLL} · Bridge: ${BR} · Reranker: ${RER}"
[ "$STALE" != "-" ] && echo "Stale feeds: $STALE | color=orange"
echo "---"
echo "Feed freshness | size=11"
echo "$H" | python3 -c 'import json,sys
for f in json.load(sys.stdin).get("feeds",[]):
    a=f.get("age_hours"); m="!" if f.get("stale") else "·"
    age=(str(a)+"h") if a is not None else "—"
    print(m+" "+f["source"]+": "+age)' 2>/dev/null
echo "---"
echo "Open dashboard | href=$URL/"
echo "Pause (free RAM) | bash=\"$CTL\" param1=pause terminal=false refresh=true"
echo "Resume | bash=\"$CTL\" param1=resume terminal=false refresh=true"
echo "Refresh | refresh=true"
