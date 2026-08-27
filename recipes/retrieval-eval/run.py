#!/usr/bin/env python3
"""Golden retrieval eval for the brains-port corpus.

Runs `gbrain search <query> --limit 10` as a subprocess against the LIVE
brain (read-only) for every row in queries.jsonl, scores hit@1/5/10 + MRR
against expected slug prefixes, counts synthesis-page poison (digests/,
scorecards/) in the top 10, and estimates freshness (median age in days of
date-bearing slugs in the top 5) for temporal queries.

Usage:
    python3 run.py --label before
    GBRAIN_SEARCH_EXCLUDE=digests/ GBRAIN_RECENCY_DEFAULT=on \
        python3 run.py --label after

Environment flags are inherited by the gbrain subprocess — that is the
intended way to A/B the exclusion/recency config without touching
~/.gbrain or the config table.

Writes results-<label>.json next to this script (timestamped).
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, date, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Synthesis-page prefixes counted as poison when they appear in results.
POISON_PREFIXES = ("digests/", "scorecards/")

# `gbrain search` result lines look like:  [1.2532] telegram/1-technical-scanner/2026-07-26 -- # snippet
RESULT_RE = re.compile(r"^\[(\d+(?:\.\d+)?)\]\s+(\S+)\s+--")

# Trailing YYYY-MM-DD in a slug (telegram/x/digests pages are date-keyed).
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})$")


def run_search(query: str, limit: int = 10, timeout: int = 120):
    """Run gbrain search and return [(score, slug), ...] in rank order."""
    proc = subprocess.run(
        ["gbrain", "search", query, "--limit", str(limit)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    hits = []
    for line in proc.stdout.splitlines():
        m = RESULT_RE.match(line.strip())
        if m:
            hits.append((float(m.group(1)), m.group(2)))
    return hits, proc.returncode


def first_expected_rank(slugs, expected_prefixes):
    for i, slug in enumerate(slugs, start=1):
        if any(slug.startswith(p) for p in expected_prefixes):
            return i
    return None


def median_age_days(slugs, today):
    ages = []
    for slug in slugs:
        m = DATE_RE.search(slug)
        if m:
            try:
                d = date.fromisoformat(m.group(1))
                ages.append((today - d).days)
            except ValueError:
                pass
    return statistics.median(ages) if ages else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="Run label (e.g. before / after) — names the results file.")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--queries", default=str(HERE / "queries.jsonl"))
    args = ap.parse_args()

    queries = []
    with open(args.queries) as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    today = date.today()
    per_query = []
    for q in queries:
        try:
            hits, rc = run_search(q["query"], limit=args.limit)
        except subprocess.TimeoutExpired:
            hits, rc = [], -1
        slugs = [s for _, s in hits]
        rank = first_expected_rank(slugs, q["expected_slugs"])
        poison = [s for s in slugs if s.startswith(POISON_PREFIXES)]
        row = {
            "id": q["id"],
            "query": q["query"],
            "temporal": bool(q.get("temporal")),
            "n_results": len(slugs),
            "rank_first_expected": rank,
            "hit@1": bool(rank == 1),
            "hit@5": bool(rank is not None and rank <= 5),
            "hit@10": bool(rank is not None and rank <= 10),
            "rr": (1.0 / rank) if rank else 0.0,
            "poison_hits_top10": len(poison),
            "poison_slugs": poison,
            "median_age_days_top5": median_age_days(slugs[:5], today),
            "top5": slugs[:5],
            "rc": rc,
        }
        per_query.append(row)
        flag = "OK " if row["hit@5"] else ("m10" if row["hit@10"] else "MISS")
        age = row["median_age_days_top5"]
        print(
            f"{flag} {q['id']:<22} rank={str(rank or '-'):<4} "
            f"poison={len(poison)} age(top5)={age if age is not None else '-'}",
            flush=True,
        )

    n = len(per_query)
    agg = {
        "n_queries": n,
        "hit@1": sum(r["hit@1"] for r in per_query) / n,
        "hit@5": sum(r["hit@5"] for r in per_query) / n,
        "hit@10": sum(r["hit@10"] for r in per_query) / n,
        "mrr": sum(r["rr"] for r in per_query) / n,
        "poison_hits_total": sum(r["poison_hits_top10"] for r in per_query),
        "queries_with_poison": sum(1 for r in per_query if r["poison_hits_top10"] > 0),
        "median_age_temporal_top5": statistics.median(
            [r["median_age_days_top5"] for r in per_query if r["temporal"] and r["median_age_days_top5"] is not None]
        ) if any(r["temporal"] and r["median_age_days_top5"] is not None for r in per_query) else None,
    }

    print("\n=== aggregate ===")
    print(f"label                {args.label}")
    print(f"hit@1                {agg['hit@1']:.2f}")
    print(f"hit@5                {agg['hit@5']:.2f}")
    print(f"hit@10               {agg['hit@10']:.2f}")
    print(f"MRR                  {agg['mrr']:.3f}")
    print(f"poison hits (top10)  {agg['poison_hits_total']} across {agg['queries_with_poison']}/{n} queries")
    print(f"median age, temporal {agg['median_age_temporal_top5']} days (top-5, date-bearing slugs)")

    out = {
        "label": args.label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "env": {
            "GBRAIN_SEARCH_EXCLUDE": os.environ.get("GBRAIN_SEARCH_EXCLUDE"),
            "GBRAIN_RECENCY_DEFAULT": os.environ.get("GBRAIN_RECENCY_DEFAULT"),
            "GBRAIN_RECENCY_DECAY": os.environ.get("GBRAIN_RECENCY_DECAY"),
        },
        "aggregate": agg,
        "per_query": per_query,
    }
    out_path = HERE / f"results-{args.label}.json"
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
