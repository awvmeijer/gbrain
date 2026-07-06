#!/bin/zsh
# BRAINS SessionStart — inject per-repo brain context. Fail-open (2s cap).
exec "/Users/awvmeijer/gbrain/sidecars/.venv/bin/python" "/Users/awvmeijer/gbrain/infra/hooks/session.py" start 2>/dev/null
