#!/usr/bin/env python3
"""List every BRAINS launchd job: real name, status, schedule, command, purpose.

macOS "Login Items / App Background Activity" only shows the *interpreter*
(bash/python/llama-server/zsh) for a launchd job, never a friendly name. This
gives the authoritative mapping so you know exactly what each background
process is, when it runs, and what it does.

    python3 infra/brains-jobs.py
"""
from __future__ import annotations

import glob
import os
import plistlib
import subprocess
from pathlib import Path

LA = Path.home() / "Library" / "LaunchAgents"

PURPOSE = {
    "com.brains.max-bridge": "Claude Max CLI bridge — OpenAI-compat shim → flat-fee Claude (always-on)",
    "com.brains.reranker": "bge-reranker-v2-m3 via llama-server — search reranking (always-on)",
    "com.brains.feeds": "Daily feeds digest — collect Discord/YouTube/X → think → post to Discord",
    "com.brain.ollama": "Ollama server — bge-m3 embeddings + qwen3 local LLM (always-on)",
}

# macOS background-activity binary → which job it actually is.
BINARY_HINT = {
    "python": "com.brains.max-bridge (sidecar venv python)",
    "llama-server": "com.brains.reranker",
    "bash": "com.brains.feeds (feeds-cron.sh, daily)",
    "ollama": "com.brain.ollama",
}


def _status(label: str) -> str:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    except Exception:
        return "?"
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2] == label:
            pid, ex = parts[0], parts[1]
            return f"pid={pid} exit={ex}" if pid != "-" else f"loaded exit={ex}"
    return "NOT LOADED"


def _schedule(d: dict) -> str:
    if d.get("KeepAlive"):
        return "always-on (KeepAlive)"
    sci = d.get("StartCalendarInterval")
    if isinstance(sci, dict):
        return f"daily {sci.get('Hour', 0):02d}:{sci.get('Minute', 0):02d}"
    if isinstance(sci, list):
        return "; ".join(f"{x.get('Hour',0):02d}:{x.get('Minute',0):02d}" for x in sci)
    si = d.get("StartInterval")
    if si:
        return f"every {si}s"
    if d.get("RunAtLoad"):
        return "at load"
    return "on-demand"


def main() -> None:
    paths = sorted(set(glob.glob(str(LA / "com.brain*.plist")) + glob.glob(str(LA / "com.brains*.plist"))))
    print(f"BRAINS launchd jobs ({len(paths)} found in {LA}):\n")
    for p in paths:
        try:
            d = plistlib.loads(Path(p).read_bytes())
        except Exception as e:
            print(f"  {Path(p).stem}: unreadable ({e})")
            continue
        label = d.get("Label", Path(p).stem)
        cmd = " ".join(d.get("ProgramArguments", []))
        purpose = PURPOSE.get(label, "(unmapped — likely an old ~/brain job)")
        print(f"● {label}")
        print(f"    status:   {_status(label)}")
        print(f"    schedule: {_schedule(d)}")
        print(f"    purpose:  {purpose}")
        print(f"    runs:     {cmd}")
        print()
    print("macOS 'App Background Activity' shows the interpreter, not the job. Map:")
    for binname, job in BINARY_HINT.items():
        print(f"    \"{binname}\"  →  {job}")
    print('\n  (zsh entries are usually transient login shells, not a brains job.)')


if __name__ == "__main__":
    main()
