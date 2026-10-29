#!/usr/bin/env python3
"""Aggregate graded calls into per-source scorecards.

Outputs:
  scorecard.json                          — machine-readable, next to this script
  <ingest>/scorecards/<YYYY-MM-DD>.md     — ingestable page; the fintwit-analyst
                                            digest appends the latest one so
                                            synthesis can weight conflicting
                                            calls by source track record.

Ranking: 30d hit rate, sources with >= MIN_GRADED calls graded at 30d.
Below that a source is listed as "insufficient history" — no rank, no verdict.
Usage: scorecard.py [ingest_dir]
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
GRADED = HERE / "graded.jsonl"
OUT_JSON = HERE / "scorecard.json"
DEFAULT_INGEST = Path("~/brains-ingest").expanduser()

MIN_GRADED = 5   # 30d-graded calls needed before a source gets ranked


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:+.1f}%"


def _rate(hits: int, n: int) -> str:
    return "—" if n == 0 else f"{100 * hits / n:.0f}% ({hits}/{n})"


def summarize(calls: list[dict]) -> dict:
    s = {"n_calls": len(calls)}
    for h in (7, 30):
        g = [c for c in calls if c.get(f"h{h}", {}).get("status") == "graded"]
        ex = [c[f"h{h}"]["excess"] for c in g if "excess" in c[f"h{h}"]]
        s[f"n_graded_{h}"] = len(g)
        s[f"hits_{h}"] = sum(1 for c in g if c[f"h{h}"]["hit"])
        s[f"hit_rate_{h}"] = round(s[f"hits_{h}"] / len(g), 3) if g else None
        s[f"avg_excess_{h}"] = round(mean(ex), 4) if ex else None
    g30 = [c for c in calls if "excess" in c.get("h30", {})]
    for key, pick in (("best_call", max), ("worst_call", min)):
        if g30:
            c = pick(g30, key=lambda c: c["h30"]["excess"])
            s[key] = {"ticker": c["ticker"], "date": c["date"],
                      "direction": c["direction"], "excess_30": c["h30"]["excess"]}
        else:
            s[key] = None
    return s


def main() -> int:
    ing = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_INGEST
    graded = [json.loads(l) for l in GRADED.open()]

    by_src: dict[str, list[dict]] = {}
    for c in graded:
        by_src.setdefault(c["source_id"], []).append(c)
    sources = {sid: summarize(cs) for sid, cs in sorted(by_src.items())}
    for s in sources.values():
        s["ranked"] = s["n_graded_30"] >= MIN_GRADED

    ranked = sorted(
        (dict(source_id=sid, **s) for sid, s in sources.items() if s["ranked"]),
        key=lambda s: (s["hit_rate_30"], s["avg_excess_30"] or 0), reverse=True)
    thin = sorted(
        (dict(source_id=sid, **s) for sid, s in sources.items() if not s["ranked"]),
        key=lambda s: -s["n_calls"])

    today = date.today().isoformat()
    unknown = sorted({c["ticker"] for c in graded if c["status"] == "unknown_ticker"})
    OUT_JSON.write_text(json.dumps({
        "generated": today,
        "params": {"horizons": [7, 30], "min_graded": MIN_GRADED,
                   "benchmark": "SPY"},
        "totals": {"calls": len(graded),
                   "graded_7": sum(s["n_graded_7"] for s in sources.values()),
                   "graded_30": sum(s["n_graded_30"] for s in sources.values())},
        "unknown_tickers": unknown,
        "sources": sources,
    }, indent=2) + "\n")

    # --- markdown page -------------------------------------------------------
    L = [
        "---",
        f"title: Creator scorecard — {today}",
        "source: scorecard",
        f"date: {today}",
        "tags: [scorecard, finance]",
        "---",
        "",
        f"# Creator scorecard — {today}",
        "",
        f"Directional ticker calls extracted from ingested YouTube digests and X "
        f"posts, graded against actual closes (Yahoo Finance) at 7d/30d horizons; "
        f"excess return is direction-adjusted vs SPY over the same window. "
        f"{len(graded)} calls on file. Telegram scanner screens and synthesized "
        f"digests are not graded.",
        "",
        f"## Ranked (≥{MIN_GRADED} calls graded at 30d)",
        "",
        "| # | Source | Calls | Hit @7d | Hit @30d | Avg excess @30d | Best (30d) | Worst (30d) |",
        "|---|--------|------:|--------:|---------:|----------------:|------------|-------------|",
    ]
    for i, s in enumerate(ranked, 1):
        b, w = s["best_call"], s["worst_call"]
        fmt = lambda c: (f"{c['ticker']} {c['direction'][:4]} "
                         f"{_pct(c['excess_30'])}") if c else "—"
        L.append(f"| {i} | {s['source_id']} | {s['n_calls']} "
                 f"| {_rate(s['hits_7'], s['n_graded_7'])} "
                 f"| {_rate(s['hits_30'], s['n_graded_30'])} "
                 f"| {_pct(s['avg_excess_30'])} | {fmt(b)} | {fmt(w)} |")
    if not ranked:
        L.append("| — | (no source has enough graded history yet) | | | | | | |")

    L += ["", "## Insufficient history (unranked)", ""]
    L.append(", ".join(f"{s['source_id']} ({s['n_calls']} calls, "
                       f"{s['n_graded_30']} graded @30d)" for s in thin) or "—")
    L += [
        "",
        "## Caveats — read before trusting the table",
        "",
        "- **Survivorship:** only creators still being ingested are graded; "
        "sources dropped for being bad are not here to look bad.",
        "- **Level-less grading:** a call is graded on direction from the "
        "call-date close only — stated entries, targets, and stops are ignored, "
        "so a \"buy at $250\" that never dipped to $250 still gets graded.",
        "- **Horizon arbitrariness:** 7/30 calendar days are our windows, not "
        "the creator's; swing calls judged at 30d and LT theses judged at 7d "
        "are both mis-measured. Intraday calls are excluded but timeframes vary.",
        "- **Extraction bias:** X prose parsing is deliberately conservative — "
        "missed calls are invisible; YouTube calls pass through a digest "
        "paraphrase before parsing.",
        "- **Close-to-close, equal-weight:** no intraday fills, no dividends, "
        "no conviction sizing; a hedged 2% starter and a max-conviction bet "
        "count the same.",
        "- **Recent calls are pending**, so the graded set skews old; excess-vs-"
        "SPY damps but does not remove market-regime and sector-beta effects.",
        f"- Ungradeable tickers skipped: {', '.join(unknown) or 'none'}.",
    ]

    page = (ing / "scorecards" / f"{today}.md")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("\n".join(L) + "\n")

    print(f"wrote {OUT_JSON} and {page}")
    print(f"ranked: {len(ranked)}, insufficient: {len(thin)}")
    for i, s in enumerate(ranked, 1):
        print(f"  {i}. {s['source_id']}: hit30 {_rate(s['hits_30'], s['n_graded_30'])}, "
              f"excess30 {_pct(s['avg_excess_30'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
