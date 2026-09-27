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
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

# Priority order: densest signal first; rss last (context budget may trim it).
SOURCES = ["telegram", "youtube", "x", "discord", "rss"]

# Feeds that publish daily: silence past this many days means a DEAD COLLECTOR,
# not a quiet market. Mirrors FEED_EXPECT_DAILY in sidecars/capture/server.py.
# `discord` is deliberately absent — the bot sees one low-traffic channel (its
# own digest channel is excluded to avoid a feedback loop), so quiet is normal.
# 2026-09-21: x had been dark 30 days and youtube 7 before anyone noticed, because
# the digest reported "no X posts present for this period" in exactly the same
# words it would use for a slow news day. A dark collector must never read like
# a quiet market.
DARK_AFTER_DAYS = {"x": 2, "youtube": 3, "telegram": 2, "rss": 2}
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
{feed_health}

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
  line — never speculate.
- Any source flagged DARK above is a BROKEN INGESTION PIPELINE, not an absence
  of market activity. Attribute it to the outage ("X: none — collector dark
  32d"), never to a quiet market, and never imply the corpus is complete
  without it."""


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


LEDGER_STALE_DAYS = 14  # scorecard ledger older than this gets a warning line


def _scorecard_block(ing: Path) -> tuple[str, str]:
    """creator-scorecard integration (recipes/creator-scorecard): append the
    latest source track-record page, if any, so synthesis can weight
    conflicting calls by each source's graded hit rate. Returns
    (block, ledger_date); ("", "") when no ledger page exists."""
    pages = sorted((ing / "scorecards").glob("????-??-??.md"))
    if not pages:
        return "", ""
    block = (
        "\n\n===== SOURCE TRACK RECORD (creator-scorecard) =====\n"
        "When sources above conflict on a ticker, weight the calls by each "
        "source's historical hit rate in this table and say so inline "
        '(e.g. "X, whose 30d hit rate is 75%, says ...").\n'
        + pages[-1].read_text(errors="ignore")[:PER_FILE_CAP]
    )
    return block, pages[-1].stem


def _coverage_footer(total: int, dropped: list[str], ledger_date: str) -> str:
    """Deterministic coverage trailer appended to every digest. The size-cap
    drop used to be a stderr-only count — 2026-08-14 silently lost 85 of 112
    pages. Triage loss must be visible in the digest itself, every day."""
    lines = [
        f"Coverage: {total - len(dropped)} of {total} pages included "
        f"({len(dropped)} dropped at size cap)"
    ]
    by_src: dict[str, list[str]] = {}
    for rel in dropped:
        src, _, rest = rel.partition("/")
        by_src.setdefault(src, []).append(rest or rel)
    for src in SOURCES:
        if src in by_src:
            lines.append(f"- dropped {src} ({len(by_src[src])}): "
                         + ", ".join(by_src[src]))
    if ledger_date:
        age = (date.today() - date.fromisoformat(ledger_date)).days
        lines.append(f"Source weights from scorecard ledger dated {ledger_date} "
                     f"({age} days old)")
        if age > LEDGER_STALE_DAYS:
            lines.append(f"WARNING: scorecard ledger is more than "
                         f"{LEDGER_STALE_DAYS} days old — source weights are "
                         "stale; rerun recipes/creator-scorecard")
    else:
        lines.append("No scorecard ledger found — source calls are unweighted")
    lines.append(_forge_heartbeat_line())
    return "\n".join(lines)


# forge2 retired 2026-09-21; the live product is forge3 (Straat), whose
# scheduler writes ~/.forge3/heartbeat.json every 10 minutes
# (forge3/agents/scheduler.py write_heartbeat). Keys: product, ts, scheduler,
# agents, and from the 10-minute job ready + failing.
FORGE_HEARTBEAT = Path.home() / ".forge3" / "heartbeat.json"
FORGE_DARK_DAYS = 3  # three consecutive dark days is an incident, not a pause


def _forge_heartbeat_line() -> str:
    """Straat liveness in the daily digest: the operator's earliest warning
    that Forge went dark or that its readiness checks are failing."""
    try:
        beat = json.loads(FORGE_HEARTBEAT.read_text())
        ts = datetime.fromisoformat(beat["ts"])
        age = datetime.now(timezone.utc) - ts
        stamp = ts.isoformat(timespec="minutes")
        if age > timedelta(days=FORGE_DARK_DAYS):
            return (f"INCIDENT: FORGE DARK — last Straat heartbeat "
                    f"{stamp} ({age.days}d ago)")
        parts = [f"{beat.get('agents', '?')} agents"]
        if beat.get("scheduler") != "on":
            parts.append(f"scheduler {beat.get('scheduler', '?')}")
        failing = beat.get("failing") or []
        if failing:
            parts.append("failing: " + ", ".join(map(str, failing)))
        elif "ready" in beat:
            parts.append("ready" if beat["ready"] == "ok" else f"ready={beat['ready']}")
        return f"Straat: last beat {stamp} ({', '.join(parts)})"
    except FileNotFoundError:
        return "INCIDENT: FORGE DARK — no Straat heartbeat file has ever been written"
    except (ValueError, KeyError, TypeError) as exc:
        return f"WARNING: Straat heartbeat unreadable ({exc!r})"


def gather(ing: Path, days: int) -> tuple[list[tuple[str, str]], list[str], dict[str, str]]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    blocks: list[tuple[str, str]] = []
    skipped: list[str] = []
    # Newest content date seen per source. Free to collect here — this walk
    # already reads and dates every page — and it is what separates a dark
    # collector from a quiet one. Deliberately NOT read from the capture
    # sidecar's /api/health: the digest must still be able to report that feeds
    # are down when the sidecar itself is down. Depending on the thing you are
    # monitoring is how the 30-day X outage stayed invisible.
    newest: dict[str, str] = {}
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
            if d and d > newest.get(src, ""):
                newest[src] = d
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
    return blocks, skipped, newest


def dark_feeds(newest: dict[str, str]) -> list[tuple[str, int | None]]:
    """Daily-publishing sources with no page inside their tolerance.

    Returns (source, days_silent); days_silent is None when the source has
    never produced a dated page at all.
    """
    today = date.today()
    out: list[tuple[str, int | None]] = []
    for src, limit in DARK_AFTER_DAYS.items():
        seen = newest.get(src)
        if not seen:
            out.append((src, None))
            continue
        try:
            age = (today - date.fromisoformat(seen)).days
        except ValueError:
            continue
        if age > limit:
            out.append((src, age))
    return sorted(out, key=lambda t: (-1 if t[1] is None else -t[1]))


COMPUTE_SUMMARY = Path.home() / "brains-console" / "sampler" / "summarize.py"


def _compute_block() -> str:
    """Yesterday's compute (GPU, memory, top services, LLM calls, Claude Code
    tokens) from the brains-console sampler. Deterministic — no LLM tokens. A
    failure prints the reason instead of dropping the section."""
    import subprocess
    try:
        r = subprocess.run([sys.executable, str(COMPUTE_SUMMARY), "--day", "yesterday", "--markdown"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return f"**⚙️ Compute** — unavailable ({type(e).__name__})"
    if r.returncode != 0 or not r.stdout.strip():
        why = (r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1][:120]
        return f"**⚙️ Compute** — unavailable ({why})"
    return r.stdout.strip()


def dark_banner(dark: list[tuple[str, int | None]]) -> str:
    if not dark:
        return ""
    parts = [f"{s}: {'no data ever' if d is None else f'{d}d silent'}" for s, d in dark]
    return ("⚠ **STALE FEEDS — ingestion is broken, not the market:** "
            + " · ".join(parts)
            + "\nThese sources contributed NOTHING to the digest below.\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ingest_dir")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--bridge", default="http://127.0.0.1:8789")
    ap.add_argument("--model", default="claude-sonnet")
    ap.add_argument("--max-bytes", type=int, default=TOTAL_CAP)
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble context, print the coverage trailer, skip the bridge")
    args = ap.parse_args()

    ing = Path(args.ingest_dir).expanduser()
    blocks, skipped, newest = gather(ing, args.days)
    dark = dark_feeds(newest)
    if not blocks:
        print("no in-window source pages found", file=sys.stderr)
        return 1

    window = f"{(date.today() - timedelta(days=args.days)).isoformat()} → {date.today().isoformat()}"
    ctx, used = [], 0
    dropped: list[str] = []
    for rel, content in blocks:
        piece = f"\n\n===== SOURCE: {rel} =====\n{content}"
        if used + len(piece) > args.max_bytes:
            dropped.append(rel)
            continue
        ctx.append(piece)
        used += len(piece)

    feed_health = ""
    if dark:
        feed_health = ("\nFEED HEALTH — these sources are DARK (collector broken, "
                       "zero pages ingested): "
                       + ", ".join(f"{s} ({'never' if d is None else f'{d}d'})" for s, d in dark)
                       + ".\n")
    user_msg = ANALYST_PROMPT.format(window=window, feed_health=feed_health) + "".join(ctx)
    sc_block, ledger_date = _scorecard_block(ing)
    user_msg += sc_block  # creator-scorecard: weight by track record
    if dropped:
        user_msg += f"\n\n[note: {len(dropped)} lower-priority source pages omitted for length]"
    if skipped:
        user_msg += (
            f"\n\n[note: excluded as recycled/out-of-window content: {', '.join(skipped)}]"
        )

    footer = _coverage_footer(len(blocks), dropped, ledger_date)
    banner = dark_banner(dark)
    if args.dry_run:
        print(f"dry-run: {len(blocks) - len(dropped)} pages, {used // 1000}KB context, "
              f"prompt {len(user_msg) // 1000}KB — bridge call skipped", file=sys.stderr)
        if banner:
            print(banner)
        print(_compute_block())
        print(footer)
        return 0

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
    print(f"context: {len(blocks) - len(dropped)} pages, {used // 1000}KB "
          f"(+{len(dropped)} dropped, {len(skipped)} stale-excluded)", file=sys.stderr)
    if banner:
        print(banner)
    print(out)
    print("\n---\n" + _compute_block() + "\n\n" + footer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
