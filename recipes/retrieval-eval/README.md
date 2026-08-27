# retrieval-eval — golden queries for the brains-port corpus

Guards against **retrieval self-poisoning**: synthesized pages (daily digests
under `digests/`, frontmatter `source: digest`) are written back into the
corpus and — being dense summaries of exactly the topics the next digest or
`think` run will query — outrank the primary sources they cite. The eval
also tracks **freshness**: for a news-heavy corpus, "latest X" queries that
return two-week-old scanner pages are wrong even when the slug prefix matches.

## Files

- `queries.jsonl` — ~15 realistic queries (fintwit tickers, scanner lookups,
  channel attribution, entity/person lookups). Each row: `id`, `query`,
  `expected_slugs` (slug **prefixes**; a hit is any result starting with any
  of them), `temporal` (freshness matters), `why`.
- `run.py` — runs `gbrain search <q> --limit 10` per query (subprocess,
  LIVE brain, read-only), scores hit@1/5/10 + MRR, counts poison
  (`digests/`, `scorecards/`) in the top 10, and reports median slug-date
  age of the top 5 for temporal queries. Writes `results-<label>.json`.
- `results-*.json` — timestamped runs (committed for the before/after pair).

## Running

```bash
# Baseline (current default behavior):
python3 run.py --label before

# With the retrieval fix flipped on (env-only; nothing persistent touched):
GBRAIN_SEARCH_EXCLUDE=digests/ GBRAIN_RECENCY_DEFAULT=on \
    python3 run.py --label after
```

The env vars are inherited by the `gbrain` subprocess. `GBRAIN_SEARCH_EXCLUDE`
is the long-standing upstream mechanism (source-boost.ts hard-excludes);
`GBRAIN_RECENCY_DEFAULT` is the brains-port addition that gives non-temporal
queries the recency post-fusion stage by default (see
`src/core/search/mode.ts`, key `search.recency_default`).

## Persistent enablement (operator action, deliberate)

```bash
gbrain config set search.exclude_slug_prefixes "digests/,scorecards/"
gbrain config set search.recency_default on
```

(`scorecards/` — frontmatter `source: scorecard` — is written by
`recipes/creator-scorecard/` and is the same synthesis class as digests:
derived from the corpus, cited by the next digest. Exclude it from day one.)

Both keys default to **unset = current behavior**. `gbrain search --include-synthetic <q>`
re-includes the excluded synthesis pages for a single query.

## Interpreting the table

- `rank` — rank of the first expected-prefix hit (drives MRR).
- `poison` — number of `digests/`/`scorecards/` slugs in the top 10.
  Target after the fix: 0 (unless `--include-synthetic`).
- `age(top5)` — median age in days of date-suffixed slugs in the top 5.
  Should drop for `temporal: true` queries once recency is on; undated
  (evergreen) pages are excluded from the metric and unaffected by design.
