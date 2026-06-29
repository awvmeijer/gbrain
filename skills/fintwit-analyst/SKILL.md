---
name: fintwit-analyst
description: Synthesize the X (fintwit), YouTube-creator, and Discord feeds into a daily actionable finance read — tickers, catalysts, what changed, and source conflicts. Volume is never a signal.
triggers:
  - "fintwit digest"
  - "finance feed digest"
  - "what are my finance follows saying"
  - "fintwit analyst"
  - "daily market read from my feeds"
  - "tickers and catalysts from my feeds"
tools:
  - query
  - search
  - think
  - list_pages
  - get_page
mutating: false
---

# Fintwit Analyst Skill

Turn the ingested feeds (`source: x`, `source: youtube`, `source: discord`) into a
tight, actionable finance read. The reader is the user — a hands-on investor who
follows ~50 fintwit handles + a handful of finance YouTube creators and wants the
signal without scrolling.

> **Hard rule (from the user's forge spec §13):** mention VOLUME is NEVER a
> buy/sell signal. Report *what was said* and *who said it*; never imply "N people
> mentioned $X, therefore buy." Surface conviction only when a source states it.

## Contract

- **Every claim is attributed** to a source page: `[handle/creator, date]` or the
  page slug. No un-sourced assertions.
- **Tickers are named explicitly** ($SYMBOL) with the **direction the source took**
  (bullish / bearish / watch) and any **level, target, or catalyst** they cited.
- **"What changed"** leads: prefer today-vs-recent deltas (new calls, reversals,
  a name moving from watch→conviction) over a flat list.
- **Conflicts are explicit**: when sources disagree on a name (e.g. one bullish,
  one fading it), show both with attribution — don't average them away.
- **No financial advice, no aggregated sentiment-as-signal.** This is a digest of
  what your follows said, not a recommendation.
- Read-only: do not create/modify brain pages unless explicitly asked.

## Phases

1. **Gather.** Pull recent feed material across the three sources. Prefer the
   reranked retrieval path:

   ```bash
   gbrain query "tickers, catalysts, market themes, calls" --rerank
   gbrain list --tag finance           # recent x/youtube/discord pages
   ```

   For YouTube, the `## Digest` section of each video page already holds the
   per-video thesis + ticker calls — lead with those over raw transcript chunks.

2. **Cluster.** Group by theme first (e.g. AI-infra/neocloud, rates/macro,
   single-stock catalysts, crypto, robotics), then by ticker within a theme.

3. **Synthesize** with `think` (deep tier → Claude via the Max bridge):

   ```bash
   gbrain think "Across my fintwit X feed, finance YouTube creators, and Discord from the last day: group by theme, name every ticker with the source's direction + any level/catalyst, lead with what CHANGED, and call out where sources conflict. Attribute every line. Do not treat mention volume as a signal."
   ```

4. **Shape the output** as:
   - **What changed** — 3-6 bullets: new calls, reversals, conviction shifts.
   - **By theme** — each ticker: `$SYM — <direction> (<source, date>): <level/catalyst/claim>`.
   - **Conflicts** — disagreements between sources, both sides attributed.
   - **Quiet/unusual** — a name getting unusual attention OR a usually-loud source going quiet (context, not a signal).

5. **Deliver.** Return the digest. When run by the daily cron it is posted to
   Discord; when run interactively, print it.

## Self-tuning (skillopt)

This skill is a candidate for `skillopt` — let the dream cycle refine the phrasing
of the phase-3 `think` directive and the output shape against which digests the
user actually engages with. Keep the Contract's hard rules (attribution,
volume-≠-signal, no advice) fixed; only the synthesis lens is tunable.
