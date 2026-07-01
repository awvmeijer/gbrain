#!/bin/bash
# brains-ctl.sh pause|resume — free/restore RAM by booting the heavy GBrain
# sidecars out/in. Ollama (local models) is the big RAM; reranker + max-bridge
# are smaller. The capture sidecar stays up so the dashboard + phone capture
# remain reachable while paused.
U=$(id -u)
LA="$HOME/Library/LaunchAgents"
JOBS=("com.brain.ollama" "com.brains.reranker" "com.brains.max-bridge")

case "$1" in
  pause)
    for j in "${JOBS[@]}"; do launchctl bootout "gui/$U/$j" 2>/dev/null; done
    echo "paused: ${JOBS[*]}"
    ;;
  resume)
    for j in "${JOBS[@]}"; do launchctl bootstrap "gui/$U" "$LA/$j.plist" 2>/dev/null; done
    echo "resumed: ${JOBS[*]}"
    ;;
  *)
    echo "usage: brains-ctl.sh pause|resume"; exit 1 ;;
esac
