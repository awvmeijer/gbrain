#!/usr/bin/env python3
"""Legacy-corpus replay (one-off backfill) — a CURATED slice of the old ~/brain
SQLite corpus → GBrain pages, deliberately skipping the RSS firehose.

Context: the GBrain rebuild started clean (no migration) on purpose — the old
`brain.db` was ~22k episodes, 18.8k of them noisy `rss`. But that clean slate
also dropped the *high-signal* history (coding sessions, commits, fintwit
bookmarks, finance snapshots, research mail, reflections). This recipe replays
ONLY that signal spine so the fresh graph gets density without the noise.

Read-only on the archive (`mode=ro&immutable=1`) — never mutates `brain.db`, and
won't fight the live `brain serve` writer on :8790. Writes markdown pages under
`~/brains-ingest/legacy/<source>/<id>.md` so the SAME pipeline the feeds use picks
them up:  `gbrain import ~/brains-ingest --no-embed` → `gbrain embed --stale` →
`recipes/entity-extract/backfill.py` (Tier-0 wires the ticker/paper graph).

Two deliberate transforms so the graph actually lights up:
  1. Cashtags in the archive are often lowercase (`$vktx`) and `finbrain_*` pages
     carry the symbol in `meta` not the body — Tier-0's regex is UPPERCASE-only.
     So we append a deterministic `Tickers: $SYM …` footer (uppercased, stopword-
     filtered) that Tier-0 will link.  Original content is left byte-intact above it.
  2. `source` is namespaced `legacy-<source>` so the backfill stays cleanly
     filterable and never bleeds into the live feeds' `source: x` / digest scope.

Idempotent: exported episode ids are recorded in a state file; re-runs skip them
(files are overwrite-safe anyway). Run:
    python recipes/legacy-replay/replay.py [--dry-run] [--limit N]
        [--sources a,b,c] [--since YYYY-MM-DD] [--max-chars N] [--force]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
ARCHIVE = HOME / "brain" / "data" / "brain.db"
OUT = HOME / "brains-ingest" / "legacy"
STATE = GBRAIN / "logs" / "legacy-replay-state.json"
LOG = GBRAIN / "logs" / "legacy-replay.log"

# High-signal sources worth carrying over. Everything NOT here (rss firehose,
# forge_* telemetry, retrieval_drift/ops_alert/eval_report ops noise, test/smoke)
# is excluded by omission — an allow-list, so unknown future sources stay out.
ALLOW = {
    "claude_session", "claude_ai",              # coding-session history
    "git_commit", "github_activity",            # what shipped
    "x_bookmark",                               # fintwit signal (the ticker-graph gold)
    "reflection",                               # synthesized insights
    "chatgpt",                                  # prior LLM conversations
    "finbrain_ticker", "finbrain_options", "finbrain_alert", "finbrain_news",
    "finbrain_technicals", "finbrain_briefing", "finbrain_regime",  # finance snapshots
    "notion_clip", "notion_capture",            # saved clips
    "ntu_mail_archive",                         # research/work correspondence
    "discord_chat",                             # older discord history
    "obsidian", "voice_turn", "image_capture", "pwa_capture",  # personal captures
    "transcript",                               # stage-2 meeting/voice transcripts
}

# type: finding — map each archive source onto a GBrain page type the pack knows.
TYPE_MAP = {
    "claude_session": "session", "claude_ai": "session",
    "reflection": "reflection",
}
def page_type(src: str) -> str:
    return TYPE_MAP.get(src, "note")

CASHTAG_ANY = re.compile(r"\$([A-Za-z]{1,5})\b")
# same stop-set as the Tier-0 extractor + a few English words that ride the
# case-insensitive scan (Tier-0 is uppercase-only so never sees these).
STOP = {"A", "I", "U", "USD", "AI", "CEO", "CFO", "IPO", "ATH", "YOLO", "EOD",
        "PM", "AM", "EPS", "GDP", "FED", "ER", "THE", "AND", "IT", "IS", "OR", "OF"}


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[legacy-replay] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"exported": []}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def _meta(raw) -> dict:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def tickers(text: str, meta: dict) -> list[str]:
    """Uppercased ticker symbols found in the body (any case) + meta.symbol/ticker."""
    syms: set[str] = set()
    for m in CASHTAG_ANY.finditer(text):
        s = m.group(1).upper()
        if s not in STOP:
            syms.add(s)
    for k in ("symbol", "ticker"):
        v = meta.get(k)
        if isinstance(v, str) and re.fullmatch(r"[A-Za-z]{1,5}", v):
            syms.add(v.upper())
    return sorted(syms)


def _yaml_str(s: str) -> str:
    """Safe single-line YAML scalar (double-quoted, escaped)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip() + '"'


def build_page(row: sqlite3.Row, max_chars: int) -> tuple[str, str]:
    """Return (relpath, markdown). relpath is under OUT and lowercase."""
    ep_id = row["id"]
    src = row["source"]
    meta = _meta(row["meta"])
    ts = int(row["ts"])
    day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    content = (row["content"] or "").strip()
    title = (row["title"] or "").strip() or content.split("\n", 1)[0][:80] or f"{src} {ep_id}"

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars].rstrip() + "\n\n_(truncated from archive)_"

    ptype = page_type(src)
    tags = ["legacy", src]
    fm = [
        "---",
        f"type: {ptype}",
        f"title: {_yaml_str(title)}",
        f"source: legacy-{src}",
        f"legacy_source: {src}",
        f"legacy_id: {ep_id}",
        f"date: {day}",
        f"imported_from: brain-archive",
    ]
    # carry a few structured fields that help downstream (repo for commits/sessions,
    # symbol for finance) — kept as frontmatter, not invented into prose.
    repo = meta.get("repo")
    if isinstance(repo, str) and repo:
        fm.append(f"repo: {_yaml_str(repo)}")
    syms = tickers(content, meta)
    if syms:
        fm.append(f"tickers: [{', '.join(syms)}]")
    fm.append(f"tags: [{', '.join(tags)}]")
    fm.append("---")

    body = [f"# {title}", ""]
    if content:
        body.append(content)
    # Tier-0 graph hook: uppercase cashtag footer (regex is uppercase-only).
    if syms:
        body += ["", "Tickers: " + " ".join(f"${s}" for s in syms)]
    # commit context (Tier-0 doesn't link repos, but keeps the page self-describing)
    if isinstance(repo, str) and repo:
        files = meta.get("files_changed") or []
        if isinstance(files, list) and files:
            body += ["", f"Repo: {repo}", "Files: " + ", ".join(map(str, files[:20]))]

    relpath = f"{src}/{ep_id}.md"          # slug → legacy/<source>/<id> (lowercase)
    return relpath, "\n".join(fm) + "\n\n" + "\n".join(body) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sources", type=str, default=None, help="comma list overriding the allow-set")
    ap.add_argument("--since", type=str, default=None, help="YYYY-MM-DD; only episodes on/after")
    ap.add_argument("--max-chars", type=int, default=8000, help="body truncation cap")
    ap.add_argument("--force", action="store_true", help="re-export already-exported ids")
    args = ap.parse_args()

    if not ARCHIVE.exists():
        log(f"FATAL: archive not found at {ARCHIVE}"); sys.exit(1)

    allow = set(s.strip() for s in args.sources.split(",")) if args.sources else ALLOW
    since_ts = None
    if args.since:
        since_ts = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    st = load_state()
    done = set(st.get("exported", []))

    con = sqlite3.connect(f"file:{ARCHIVE}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    q = "SELECT id, ts, source, source_id, title, content, meta FROM episodes WHERE source IN (%s)" % (
        ",".join("?" * len(allow)))
    params: list = list(allow)
    if since_ts:
        q += " AND ts >= ?"; params.append(since_ts)
    q += " ORDER BY ts ASC"
    rows = con.execute(q, params).fetchall()
    con.close()
    log(f"archive: {len(rows)} episodes across {len(allow)} allowed sources"
        + (f" since {args.since}" if args.since else ""))

    written = skipped = 0
    per_src: dict[str, int] = {}
    for row in rows:
        if args.limit and written >= args.limit:
            break
        if row["id"] in done and not args.force:
            skipped += 1
            continue
        relpath, md = build_page(row, args.max_chars)
        per_src[row["source"]] = per_src.get(row["source"], 0) + 1
        if args.dry_run:
            written += 1
            continue
        p = OUT / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md)
        done.add(row["id"]); written += 1
        if written % 200 == 0:
            st["exported"] = sorted(done); save_state(st); log(f"  …{written} written")

    if not args.dry_run:
        st["exported"] = sorted(done); save_state(st)

    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(per_src.items(), key=lambda kv: -kv[1]))
    verb = "WOULD write" if args.dry_run else "wrote"
    log(f"{verb} {written} pages ({skipped} already exported). By source: {breakdown}")
    if not args.dry_run:
        log(f"next: gbrain import {HOME}/brains-ingest --no-embed && gbrain embed --stale "
            f"&& python {GBRAIN}/recipes/entity-extract/backfill.py")


if __name__ == "__main__":
    main()
