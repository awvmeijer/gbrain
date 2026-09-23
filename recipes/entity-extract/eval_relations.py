#!/usr/bin/env python3
"""Precision of the LLM relations pass, from human labels on the shadow log.

The pass runs in SHADOW mode (logs/llm-relations-shadow.jsonl) until it earns the
right to write: at least MIN_LABELLED gate-passing proposals labelled by a human
("label": "correct" | "wrong") and precision >= TARGET. Precision is reported
with a Wilson 95% interval so a small sample can't masquerade as a result.

    python eval_relations.py            # report; exit 0 = ready to --write, 1 = not yet
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

SHADOW = Path.home() / "gbrain" / "logs" / "llm-relations-shadow.jsonl"
TARGET = 0.90
MIN_LABELLED = 50


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d, (c + r) / d


def main() -> int:
    rows = [json.loads(l) for l in SHADOW.read_text().splitlines() if l.strip()] if SHADOW.exists() else []
    gate = Counter(r.get("gate") for r in rows)
    passed = [r for r in rows if r.get("gate") == "ok"]
    labelled = [r for r in passed if r.get("label") in ("correct", "wrong")]
    correct = sum(r["label"] == "correct" for r in labelled)
    n = len(labelled)
    prec = correct / n if n else float("nan")
    lo, hi = wilson(correct, n)
    print(f"shadow proposals: {len(rows)} · passed the gate: {len(passed)} · dropped: "
          f"{ {k: v for k, v in gate.items() if k != 'ok'} or 'none'}")
    print(f"labelled (gate-passed): {n} / {MIN_LABELLED} needed · correct {correct}")
    if n:
        print(f"precision {prec:.0%} (95% CI {lo:.0%}–{hi:.0%}) · target {TARGET:.0%}")
    by_pred = Counter((r["predicate"], r["label"]) for r in labelled)
    for pred in sorted({p for p, _ in by_pred}):
        c, w = by_pred[(pred, "correct")], by_pred[(pred, "wrong")]
        print(f"  {pred:15} {c} correct · {w} wrong")
    ready = n >= MIN_LABELLED and prec >= TARGET
    print("READY to run with --write" if ready else "NOT READY — keep the pass in shadow mode")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
