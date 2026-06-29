"""Manual capture → GBrain: drain the iCloud "BrainCapture" folder into the brain.

The compliant one-tap path for content you can't auto-ingest (Discord member-only
rooms, a tweet, a paragraph from anywhere): an iOS Shortcut saves shared text as a
file into iCloud Drive/BrainCapture/. iCloud syncs it to the Mac; this script wraps
each file into a brains-ingest markdown page and archives the original so it isn't
re-imported. See README.md for the Shortcut build steps.

- Source: ~/Library/Mobile Documents/com~apple~CloudDocs/BrainCapture/ (created if
  missing, so the Shortcut always has a target).
- Output: <output_dir>/capture/<timestamp>-<slug>.md (frontmatter source: capture).
- Processed files move to BrainCapture/_archive/ (the move syncs back to iOS, so the
  capture folder self-empties). `gbrain import` also dedups by content hash.

Usage:
    python import_captures.py <output_dir>
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "BrainCapture"
TEXT_EXTS = {".txt", ".md", ".text"}


def _slug(s: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in (s or "")).strip()
    return "-".join(s.lower().split())[:48] or "capture"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    args = ap.parse_args()

    SRC.mkdir(parents=True, exist_ok=True)
    archive = SRC / "_archive"
    archive.mkdir(exist_ok=True)

    out_dir = Path(args.output_dir) / "capture"
    out_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in SRC.iterdir() if p.is_file() and p.suffix.lower() in TEXT_EXTS]
    if not files:
        print("no captures pending")
        return

    written = 0
    for p in sorted(files, key=lambda x: x.stat().st_mtime):
        try:
            content = p.read_text(errors="replace").strip()
        except Exception as e:  # unreadable file — skip, leave in place
            print(f"  skip {p.name}: {e}", file=sys.stderr)
            continue
        if not content:
            p.rename(archive / p.name)
            continue
        ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        first = content.splitlines()[0][:60]
        page = (
            f"---\n"
            f"title: Capture — {first} ({ts:%Y-%m-%d})\n"
            f"source: capture\n"
            f"date: {ts:%Y-%m-%d}\n"
            f"captured_at: {ts:%Y-%m-%dT%H:%M:%SZ}\n"
            f"tags: [capture, manual]\n"
            f"---\n\n"
            f"# Capture — {ts:%Y-%m-%d %H:%M}\n\n"
            f"{content}\n"
        )
        name = f"{ts:%Y%m%d-%H%M%S}-{_slug(first)}.md"
        (out_dir / name).write_text(page)
        p.rename(archive / p.name)  # syncs the removal back to iOS
        written += 1

    print(f"captures imported: {written} | output: {out_dir}")


if __name__ == "__main__":
    main()
