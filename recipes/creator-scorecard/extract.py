#!/usr/bin/env python3
"""Extract dated directional ticker calls from ingested creator pages.

Sources walked (under the ingest dir, default ~/brains-ingest):
  youtube/<channel>/<videoid>.md — parse the structured "## Digest" bullet
      lines ("**GLW (Corning)** — Bullish; ...for"). Reliable  pages with
      `has_digest: true`; pages flagged `stale_content: true` (recycled
      re-premiered streams) are excluded — their date lies about the call date.
  x/<handle>/<date>.md — daily post pages. Prose parsing is CONSERVATIVE:
      a bullet counts only when it names exactly ONE cashtag and contains an
      unambiguous directional phrase ("bullish", "I'm buying", "shorting", …).
      Neutral/"watch"/question bullets and multi-ticker news recaps are skipped.

Deliberately NOT graded:
  telegram/  — scanner ticker screens carry no direction; a screen is not a call.
  digests/   — synthesized from the above; grading them double-counts sources.

Output: calls.jsonl next to this script, one JSON object per line:
  {source_id, date, ticker, direction, level, target, page, context}
Dedup key: (source_id, date, ticker, direction).

Usage: extract.py [ingest_dir]
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_INGEST = Path("~/brains-ingest").expanduser()
OUT = HERE / "calls.jsonl"

# Uppercase tokens that look like tickers but (almost) never are, in this
# corpus. Includes $AI (C3.ai) — dropping the occasional real C3.ai call is
# cheaper than grading every "AI" mention. Conservatism is the point.
DENYLIST = {
    "A", "I", "AI", "AGI", "AH", "AM", "PM", "ATH", "ATL", "API", "B2B",
    "CEO", "CFO", "CTO", "COO", "CPI", "PPI", "PCE", "GDP", "FED", "FOMC",
    "SEC", "DOJ", "IRS", "CFTC", "WSJ", "CNBC", "USA", "US", "UK", "EU",
    "YOY", "QOQ", "MOM", "EPS", "PE", "PS", "PEG", "EV", "IPO", "ETF",
    "ETFS", "GAAP", "FCF", "TCV", "RDV", "ROI", "ROE", "CAGR", "RSI",
    "MACD", "SMA", "EMA", "VWAP", "OTM", "ITM", "DTE", "YTD", "MTD", "WTD",
    "DCA", "HODL", "FUD", "FOMO", "IMO", "LOL", "OMG", "PSA", "TLDR",
    "GPU", "CPU", "HBM", "DRAM", "NAND", "SAAS", "REIT", "NATO", "OPEC",
    "Q1", "Q2", "Q3", "Q4", "FY", "H1", "H2", "EST", "EDT", "PT", "MAG",
    "OP", "DM", "RT", "EOD", "EOY", "EOW", "ER", "PR", "IV", "LEAPS",
}

TICKER_RE = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$")

# --- YouTube digest bullets --------------------------------------------------
BULLET_RE = re.compile(r"^\s*[-*]\s+\*\*(.+?)\*\*\s*(?:\((.*?)\)\s*)?[—–:-]+\s*(.+)$")
# Verdict words that make a bullet ungradeable — hedged, directionless, or on
# a timescale (intraday scalps) our 7d/30d horizons can't grade.
SKIP_WORDS = ("watch", "neutral", "mixed", "cautious", "hold", "intraday")

# --- X prose (conservative) --------------------------------------------------
CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
X_BULLET_RE = re.compile(r"^- \*\*\d{2}:\d{2}\*\*", re.M)
URL_TAIL_RE = re.compile(r"\s*\(https?://\S+\)\s*$")
BULLISH_RE = re.compile(
    r"\bbullish\b|\bi'?m buying\b|\bi am buying\b|\bi (?:just )?bought\b"
    r"|\bbuying more\b|\badd(?:ed|ing) (?:more|to my)\b|\bgoing long\b"
    r"|\bi'?m long\b|\bload(?:ed|ing) up\b|\bbuy(?:ing)? the dip\b"
    r"|\baccumulating\b|\bstarted a position\b|\bnew position in\b"
    r"|\bundervalued\b",
    re.I,
)
BEARISH_RE = re.compile(
    r"\bbearish\b|\bi'?m selling\b|\bi (?:just )?sold\b|\bsold my\b"
    r"|\bselling my\b|\bshort(?:ing|ed)?\s+\$|\bi'?m short\b"
    r"|\bbought puts\b|\bbuying puts\b|\bputs on\b|\bovervalued\b"
    r"|\bstay away\b|\bavoid\b",
    re.I,
)
X_SKIP_RE = re.compile(
    r"\bwatch(?:ing)?\b|\bneutral\b|\bsidelines?\b|\bno position\b", re.I
)
# "did not close with a bullish X", "not bearish" — negation within a few words
# of the directional term flips/undoes it; too ambiguous, skip the bullet.
NEGATED_RE = re.compile(
    r"\b(?:not?|isn'?t|wasn'?t|didn'?t|did not|no longer)\b[^.!?]{0,40}"
    r"\b(?:bullish|bearish|long|short)\b",
    re.I,
)

LEVEL_RE = re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)")
TARGET_RE = re.compile(r"target[^$0-9]{0,12}\$?([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)


def _fm(text: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+?)\s*$", text, re.M)
    return m.group(1) if m else ""


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _tickers_from_bold(name: str) -> list[str]:
    """'MU (Micron)' -> [MU]; 'MSFT / GOOGL / Shopify' -> [MSFT, GOOGL];
    'SK Hynix' / 'Semiconductors (SOX/sector)' -> []."""
    name = re.sub(r"\(.*?\)", "", name)  # strip parentheticals first
    out = []
    for part in name.split("/"):
        tok = part.strip().lstrip("$").rstrip(".,:")
        if TICKER_RE.match(tok) and tok not in DENYLIST:
            out.append(tok)
    return out


def _classify_verdict(desc: str) -> str | None:
    verdict = desc.split(";", 1)[0].lower()
    if any(w in verdict for w in SKIP_WORDS):
        return None
    bull, bear = "bullish" in verdict, "bearish" in verdict
    if bull == bear:  # neither, or contradictory
        return None
    return "bullish" if bull else "bearish"


def extract_youtube(root: Path) -> list[dict]:
    calls = []
    for p in sorted(root.rglob("*.md")):
        text = p.read_text(errors="ignore")
        if _fm(text, "stale_content") == "true":   # recycled stream: date is a lie
            continue
        if _fm(text, "has_digest") != "true":
            continue
        d, channel = _fm(text, "date"), _fm(text, "channel")
        if not re.match(r"\d{4}-\d{2}-\d{2}$", d) or not channel:
            continue
        m = re.search(r"## Digest\n(.*?)(?=\n## |\Z)", text, re.S)
        if not m:
            continue
        sid = f"youtube/{channel if channel.startswith('@') else '@' + channel}"
        for line in m.group(1).splitlines():
            bm = BULLET_RE.match(line)
            if not bm:
                continue
            name, _paren, desc = bm.groups()
            direction = _classify_verdict(desc)
            if not direction:
                continue
            tm = TARGET_RE.search(desc)
            lm = LEVEL_RE.search(desc)
            for tkr in _tickers_from_bold(name):
                calls.append({
                    "source_id": sid, "date": d, "ticker": tkr,
                    "direction": direction,
                    "level": _num(lm.group(1) if lm else None),
                    "target": _num(tm.group(1) if tm else None),
                    "page": str(p), "context": desc[:160],
                })
    return calls


def extract_x(root: Path) -> list[dict]:
    calls = []
    for p in sorted(root.rglob("*.md")):
        text = p.read_text(errors="ignore")
        d, handle = _fm(text, "date"), _fm(text, "handle")
        if not re.match(r"\d{4}-\d{2}-\d{2}$", d) or not handle:
            continue
        sid = f"x/@{handle.lstrip('@')}"
        starts = [mm.start() for mm in X_BULLET_RE.finditer(text)]
        for i, s in enumerate(starts):
            bullet = text[s:starts[i + 1] if i + 1 < len(starts) else len(text)]
            bullet = html.unescape(" ".join(bullet.split()))
            bullet = URL_TAIL_RE.sub("", bullet)
            if bullet.endswith("?"):            # musing, not a call
                continue
            tags = {t for t in CASHTAG_RE.findall(bullet) if t not in DENYLIST}
            if len(tags) != 1:                  # multi-ticker recap or no ticker
                continue
            if X_SKIP_RE.search(bullet) or NEGATED_RE.search(bullet):
                continue
            bull = bool(BULLISH_RE.search(bullet))
            bear = bool(BEARISH_RE.search(bullet))
            if bull == bear:                    # neither, or contradictory
                continue
            tm = TARGET_RE.search(bullet)
            calls.append({
                "source_id": sid, "date": d, "ticker": tags.pop(),
                "direction": "bullish" if bull else "bearish",
                "level": None, "target": _num(tm.group(1) if tm else None),
                "page": str(p), "context": bullet[:160],
            })
    return calls


def main() -> int:
    ing = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_INGEST
    calls = extract_youtube(ing / "youtube") + extract_x(ing / "x")

    seen, deduped = set(), []
    for c in calls:
        key = (c["source_id"], c["date"], c["ticker"], c["direction"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    deduped.sort(key=lambda c: (c["source_id"], c["date"], c["ticker"]))
    with OUT.open("w") as f:
        for c in deduped:
            f.write(json.dumps(c) + "\n")

    by_src = Counter(c["source_id"] for c in deduped)
    print(f"extracted {len(deduped)} calls ({len(calls) - len(deduped)} dups dropped) "
          f"from {len(by_src)} sources -> {OUT}")
    for src, n in by_src.most_common():
        print(f"  {n:4d}  {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
