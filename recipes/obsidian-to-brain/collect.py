#!/usr/bin/env python3
"""Obsidian vault → brains-ingest staging (deterministic, no LLM, no network).

Watches the same vault subdirs the legacy brain watched — inbox/, Daily logs/,
Notes/, Areas/ — and stages changed .md files into <out>/obsidian/<relpath>,
where the normal feeds-cron `gbrain import` picks them up. Never modifies the
vault; never deletes staged pages (the brain is append-only from its POV).

Frontmatter: existing frontmatter is preserved verbatim; we only ADD keys the
brain relies on when they're missing (title from filename, source: obsidian,
date from file mtime, tags: [obsidian]).

State: <out>/obsidian/.state.json maps vault-relative path → content sha256.
First run stages the entire vault (historical backfill by construction).

Usage:
    python3 collect.py <output_dir> [--vault ~/BrainVault]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

WATCH_DIRS = ["inbox", "Daily logs", "Notes", "Areas"]
SKIP_PARTS = {".obsidian", "_System", "imports", "brain-view", ".trash"}
MAX_BYTES = 1_000_000  # skip giant files (embedded base64 etc.)


def _slugify(rel: Path) -> Path:
    parts = []
    for p in rel.parts:
        s = re.sub(r"[^A-Za-z0-9._-]+", "-", p).strip("-") or "note"
        parts.append(s)
    return Path(*parts)


def _ensure_frontmatter(text: str, title: str, mtime: float) -> str:
    date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    if text.startswith("---\n"):
        try:
            head, rest = text[4:].split("\n---\n", 1)
        except ValueError:
            head, rest = None, None
        if head is not None:
            add = []
            if not re.search(r"^title:", head, re.M):
                add.append(f'title: "{title}"')
            if not re.search(r"^source:", head, re.M):
                add.append("source: obsidian")
            if not re.search(r"^date:", head, re.M):
                add.append(f"date: {date}")
            if not re.search(r"^tags:", head, re.M):
                add.append("tags: [obsidian]")
            head = head.rstrip("\n") + ("\n" + "\n".join(add) if add else "")
            return f"---\n{head}\n---\n{rest}"
    return (
        f'---\ntitle: "{title}"\nsource: obsidian\ndate: {date}\n'
        f"tags: [obsidian]\n---\n\n{text}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--vault", default=str(Path.home() / "BrainVault"))
    args = ap.parse_args()

    vault = Path(args.vault).expanduser()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 1
    out = Path(args.output_dir).expanduser() / "obsidian"
    state_path = out / ".state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    staged = skipped = 0
    for wd in WATCH_DIRS:
        base = vault / wd
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.md")):
            if SKIP_PARTS.intersection(f.relative_to(vault).parts):
                continue
            if f.stat().st_size > MAX_BYTES:
                continue
            rel = f.relative_to(vault)
            body = f.read_text(errors="ignore")
            digest = hashlib.sha256(body.encode()).hexdigest()
            if state.get(str(rel)) == digest:
                skipped += 1
                continue
            dest = out / _slugify(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(_ensure_frontmatter(body, f.stem, f.stat().st_mtime))
            state[str(rel)] = digest
            staged += 1

    out.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1))
    print(f"obsidian: {staged} staged, {skipped} unchanged | vault: {vault}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
