#!/usr/bin/env python3
"""Daily fintwit digest — deterministic context, no retrieval roulette.

Why this exists: the digest used to be `gbrain think --since 2d`, i.e. semantic
retrieval over the whole brain. That starved itself two ways:
  1. Bare ticker-list pages (Telegram scans: "1. AKBA 2. ATRC …") embed terribly
     against a long thematic prompt, so the freshest signal lost the ranking
     contest — the 2026-07-07 digest declared "no scanner in window" while an
     in-window scan sat embedded in the DB.
  2. Prior digests are imported as pages, match the digest vocabulary better
     than raw sources, and get cited as evidence of absence — a self-poisoning
     loop (digest N cites data-limited digest N-1 as proof there's no data).

The collectors already know exactly what's fresh: it's on disk with a
frontmatter `date:`. So this recipe assembles the in-window pages
deterministically and hands them to the Max bridge for synthesis. Retrieval is
for questions; digests have a known corpus.

Usage:
    digest.py <ingest_dir> [--days 2] [--bridge http://127.0.0.1:8789]
Prints digest markdown to stdout; non-zero exit (and empty stdout) on failure
so the caller can fall back.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

# Priority order: densest signal first; rss last (context budget may trim it).
SOURCES = ["telegram", "youtube", "x", "discord", "rss"]
EXCLUDE_NOTE = "stale_content: true"  # recycled/re-premiered YouTube streams
PER_FILE_CAP = 4_000
# Keep total near the empirically-safe max-bridge payload (~60KB transcripts
# summarize fine; ~180KB hard-times-out the SDK subprocess). Priority order
# above means trimming costs rss/discord tail, not scanner/YouTube/X signal.
TOTAL_CAP = 60_000

ANALYST_PROMPT = """You are a fintwit analyst. Below is the COMPLETE set of my ingested finance
sources for {window} (X/fintwit feed, finance YouTube creators, Telegram
scanner channels, Discord, finance RSS). This is the full corpus — do not
claim data is missing if it appears below; equally, do not invent data that
is not below.

Write the daily digest:
- Group by theme; name every ticker with the source's direction
  (bullish/bearish/watch) plus any level or catalyst.
- Treat Telegram scanner ticker lists as a SCREEN (no direction implied).
- LEAD with OVERLAPS: any scanner-flagged ticker that an X/YouTube source
  also has a directional take on.
- Then "What changed": new calls, reversals, conviction shifts vs prior days
  if visible in the sources.
- Call out source conflicts, both sides attributed.
- Attribute every line to its source as [<relative-path>].
- Do NOT treat mention volume as a buy/sell signal.
- If a section genuinely has no supporting data in the corpus, say so in one
  line — never speculate."""


def _fm_date(text: str) -> str:
    m = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    return m.group(1) if m else ""


def _youtube_slice(text: str) -> str:
    """Frontmatter + Digest section only — transcripts would blow the budget."""
    fm = text.split("---", 2)
    head = f"---{fm[1]}---" if len(fm) >= 3 else ""
    m = re.search(r"## Digest\n(.*?)(?=\n## |\Z)", text, re.S)
    body = m.group(1).strip() if m else text[len(head):][:2_000]
    return f"{head}\n\n## Digest\n{body}"


def gather(ing: Path, days: int) -> tuple[list[tuple[str, str]], list[str]]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    blocks: list[tuple[str, str]] = []
    skipped: list[str] = []
    for src in SOURCES:
        root = ing / src
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            d = _fm_date(text)
            if not d or d < cutoff:
                continue
            rel = str(p.relative_to(ing)).removesuffix(".md")
            if EXCLUDE_NOTE in text:
                skipped.append(rel)
                continue
            content = _youtube_slice(text) if src == "youtube" else text
            if len(content) > PER_FILE_CAP:
                content = content[:PER_FILE_CAP] + "\n[truncated]"
            blocks.append((rel, content))
    return blocks, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ingest_dir")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--bridge", default="http://127.0.0.1:8789")
    ap.add_argument("--model", default="claude-sonnet")
    ap.add_argument("--max-bytes", type=int, default=TOTAL_CAP)
    args = ap.parse_args()

    ing = Path(args.ingest_dir).expanduser()
    blocks, skipped = gather(ing, args.days)
    if not blocks:
        print("no in-window source pages found", file=sys.stderr)
        return 1

    window = f"{(date.today() - timedelta(days=args.days)).isoformat()} → {date.today().isoformat()}"
    ctx, used = [], 0
    dropped = 0
    for rel, content in blocks:
        piece = f"\n\n===== SOURCE: {rel} =====\n{content}"
        if used + len(piece) > args.max_bytes:
            dropped += 1
            continue
        ctx.append(piece)
        used += len(piece)

    user_msg = ANALYST_PROMPT.format(window=window) + "".join(ctx)
    if dropped:
        user_msg += f"\n\n[note: {dropped} lower-priority source pages omitted for length]"
    if skipped:
        user_msg += (
            f"\n\n[note: excluded as recycled/out-of-window content: {', '.join(skipped)}]"
        )

    try:
        r = httpx.post(
            f"{args.bridge}/v1/chat/completions",
            json={"model": args.model,
                  "messages": [{"role": "user", "content": user_msg}]},
            timeout=600,  # big context + the bridge serializes through one claude proc
        )
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001 — caller falls back to `gbrain think`
        print(f"bridge digest failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not out:
        print("bridge returned empty digest", file=sys.stderr)
        return 1
    print(f"context: {len(blocks) - dropped} pages, {used // 1000}KB "
          f"(+{dropped} dropped, {len(skipped)} stale-excluded)", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
