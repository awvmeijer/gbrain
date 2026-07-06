#!/bin/zsh
# BRAINS SessionEnd — record one immutable session page. Fail-open (2s cap).
exec "/Users/awvmeijer/gbrain/sidecars/.venv/bin/python" "/Users/awvmeijer/gbrain/infra/hooks/session.py" end 2>/dev/null
