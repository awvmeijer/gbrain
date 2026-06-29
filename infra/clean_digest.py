#!/usr/bin/env python3
"""Clean `gbrain think` stdout for posting to Discord.

think prints the echoed question as a leading `# ...` heading, sometimes dumps a
raw JSON object instead of prose, and appends a `--- Model: …` footer. This
strips the echo + footer and, if the body is JSON, renders answer + gaps as
readable markdown. Reads stdin, writes clean markdown to stdout.
"""
import json
import re
import sys

raw = sys.stdin.read()

# 1. Drop the trailing "--- / Model: …" footer.
raw = re.split(r"\n-{3,}\s*\nModel:", raw)[0].strip()

# 2. Drop a leading single-# question echo (think prints "# <question>").
lines = raw.split("\n")
if lines and lines[0].lstrip().startswith("# ") and not lines[0].lstrip().startswith("## "):
    lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    raw = "\n".join(lines).strip()

# 3. If the body is a raw JSON object, render answer + gaps.
if raw.startswith("{"):
    try:
        d = json.loads(raw)
        out = []
        ans = (d.get("answer") or "").strip()
        if ans:
            out.append(ans)
        gaps = [g for g in (d.get("gaps") or []) if isinstance(g, str)]
        if gaps:
            out.append("\n**Gaps**\n" + "\n".join(f"- {g}" for g in gaps[:6]))
        raw = "\n".join(out).strip() or "_Thin window — little to report._"
    except Exception:
        pass

print(raw if raw.strip() else "_Nothing to report._")
