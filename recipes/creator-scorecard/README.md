# creator-scorecard

Grades fintwit/YouTube creators' historical ticker calls against actual price
history, ranks sources by hit rate, and feeds the ranking back into the daily
digest so conflicting calls get weighted by track record instead of vibes.

## Pipeline

```
~/brains-ingest/{youtube,x}   →  extract.py  →  calls.jsonl
calls.jsonl + Yahoo Finance   →  grade.py    →  graded.jsonl  (+ cache/)
graded.jsonl                  →  scorecard.py →  scorecard.json
                                             →  ~/brains-ingest/scorecards/<date>.md
```

The scorecard page is a normal ingest page (frontmatter `source: scorecard`).
`recipes/fintwit-analyst/digest.py` appends the latest one to the digest
context (`_scorecard_block`) with an instruction to weight conflicting calls
by each source's graded hit rate.

## Run

```bash
PY=~/gbrain/sidecars/.venv/bin/python          # has httpx
$PY extract.py     # walk pages -> calls.jsonl (dedup source/date/ticker/dir)
$PY grade.py       # fetch closes (cached), grade @7d/@30d -> graded.jsonl
$PY scorecard.py   # -> scorecard.json + ~/brains-ingest/scorecards/<today>.md
```

`grade.py --refresh` ignores `cache/` (needed after new calls age past a
horizon — cached price series end at their fetch date). Re-running the whole
pipeline is idempotent; the scorecard page is rewritten per calendar day.

## What counts as a call

- **YouTube** — only pages with `has_digest: true`; parses the structured
  `## Digest` bullets (`**MU (Micron)** — Bearish; ...`). Multi-ticker bullets
  (`**MSFT / GOOGL**`) fan out. Verdicts containing watch / neutral / mixed /
  cautious / hold / intraday are skipped. Pages with `stale_content: true`
  (recycled streams) are excluded — their date lies.
- **X** — a bullet must contain exactly ONE cashtag plus an unambiguous
  directional phrase ("bullish", "I'm buying", "shorting", "overvalued", …).
  Questions, negations ("did not close with a bullish…"), "sidelines",
  watch/neutral, and multi-ticker news recaps are skipped.
- **Not graded:** `telegram/` (scanner screens carry no direction) and
  `digests/` (synthesized from the sources — grading them double-counts).

## Grading

- Prices: Yahoo Finance chart API (free, keyless; plain `Mozilla/5.0` UA —
  full browser UA strings get 429'd), ~1 req/s, cached to `cache/<TICKER>.json`.
  Per-ticker fallback: stooq.com daily CSV. Unknown tickers skip gracefully.
- Entry = nearest close ON/AFTER call date (≤5d tolerance); exit = nearest
  close ON/AFTER call date + 7/30 calendar days (≤7d tolerance). Calls younger
  than a horizon are **pending**, not graded.
- bullish hit ⟺ exit > entry; bearish inverse. Excess = direction-adjusted
  return minus SPY over the same window, so a bull-market cheerleader who only
  matches beta earns ~0 excess.
- Ranking needs ≥5 calls graded @30d; below that a source is listed as
  "insufficient history".

## Limitations (also printed on every scorecard page)

Survivorship (only still-ingested creators are graded); level-less grading
(direction from call-date close only — entries/targets/stops ignored); horizon
arbitrariness (7/30d are our windows, not the creator's); conservative
extraction (missed calls are invisible; YouTube passes through a digest
paraphrase); close-to-close, equal-weight, no dividends; graded set skews old
because recent calls are pending.
