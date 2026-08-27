#!/usr/bin/env python3
"""hermes-to-brain — mirror Hermes Agent sessions into the brain (nightly).

The brain is canonical memory; Hermes's ~/.hermes/ state is a working cache.
Real-time salient facts flow through brain_capture (persona discipline); this
collector is the safety net that mirrors everything else:

  * session/conversation logs  → one page per session under hermes/sessions/
  * MEMORY.md / USER.md deltas → a page per changed file under hermes/memory/

Idempotent: pages are keyed by session id + content hash; unchanged sessions
and memory files are skipped (state in logs/hermes-to-brain-seen.json).
Wired into infra/feeds-cron.sh; the cron's `gbrain import` picks the files up.

Hermes's on-disk layout varies by version — we look in the common spots and
skip silently if absent (fail-open, like every other collector).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
HERMES = HOME / ".hermes"
OUT = HOME / "brains-ingest" / "hermes"
SEEN_FILE = HOME / "gbrain" / "logs" / "hermes-to-brain-seen.json"
MAX_PAGE_CHARS = 60_000  # keep pages embeddable; sessions longer than this get tail-truncated

# Candidate locations for session/conversation logs across Hermes versions.
SESSION_DIRS = [HERMES / "sessions", HERMES / "conversations", HERMES / "history",
                HERMES / "logs" / "sessions"]
MEMORY_FILES = [HERMES / "memories" / "MEMORY.md", HERMES / "memories" / "USER.md",
                HERMES / "MEMORY.md", HERMES / "USER.md", HERMES / "SOUL.md"]


def slugify(s: str, n: int = 48) -> str:
    s = re.sub(r"[^\w]+", "-", s.lower()).strip("-")
    return s[:n] or "x"


def load_seen() -> dict:
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
        return {}


def render_session(path: Path) -> str:
    """Best-effort render: JSON/JSONL transcripts become 'role: text' lines;
    anything else is included verbatim."""
    raw = path.read_text(errors="replace")
    lines: list[str] = []
    if path.suffix in (".json", ".jsonl"):
        records = []
        try:
            data = json.loads(raw)
            records = data if isinstance(data, list) else data.get("messages", [])
        except Exception:
            for ln in raw.splitlines():
                try:
                    records.append(json.loads(ln))
                except Exception:
                    continue
        for r in records:
            if not isinstance(r, dict):
                continue
            role = r.get("role") or r.get("author") or r.get("type") or "?"
            content = r.get("content") or r.get("text") or ""
            if isinstance(content, list):  # OpenAI-style content parts
                content = " ".join(str(p.get("text", "")) if isinstance(p, dict) else str(p)
                                   for p in content)
            content = str(content).strip()
            if content:
                lines.append(f"**{role}:** {content}")
        if lines:
            return "\n\n".join(lines)
    return raw


def write_page(rel_dir: str, name: str, title: str, body: str, kind: str) -> None:
    body = body.strip()
    if len(body) > MAX_PAGE_CHARS:
        body = "…(truncated head)…\n\n" + body[-MAX_PAGE_CHARS:]
    d = OUT / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text("\n".join([
        "---",
        f"title: {json.dumps(title)}",
        "source: hermes",
        f"kind: {kind}",
        f"date: {datetime.now().strftime('%Y-%m-%d')}",
        "tags: [hermes]",
        "---",
        "",
        body,
    ]) + "\n")


def main() -> int:
    seen = load_seen()
    wrote = 0

    # --- sessions ---
    session_files: list[Path] = []
    for d in SESSION_DIRS:
        if d.is_dir():
            session_files += [p for p in d.rglob("*") if p.is_file()
                              and p.suffix in (".json", ".jsonl", ".md", ".txt")]
    for p in sorted(session_files):
        # request_dump_* are raw API request logs (tool schemas, debug) — noise,
        # not conversation. Real transcripts don't carry that prefix.
        if p.name.startswith(("request_dump", "response_dump")):
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        h = hashlib.sha256(raw).hexdigest()
        key = f"session:{p}"
        if seen.get(key) == h:
            continue
        day = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        name = f"{day}-{slugify(p.stem)}-{h[:8]}"  # hash suffix: no collisions on truncated stems
        write_page("sessions", name, f"Hermes session — {p.stem} ({day})",
                   render_session(p), "session")
        seen[key] = h
        wrote += 1

    # --- memory files (MEMORY.md / USER.md / SOUL.md) ---
    for p in MEMORY_FILES:
        if not p.is_file():
            continue
        raw = p.read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        key = f"memory:{p}"
        if seen.get(key) == h:
            continue
        day = datetime.now().strftime("%Y-%m-%d")
        write_page("memory", f"{day}-{slugify(p.stem)}",
                   f"Hermes {p.name} snapshot — {day}",
                   raw.decode(errors="replace"), "memory-snapshot")
        seen[key] = h
        wrote += 1

    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen))
    print(f"hermes-to-brain: {wrote} page(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
