---
name: weekly-reflection
description: Review the week's brain and write a reflection page — recurring themes, what changed, which feed calls played out, conviction shifts, and what the user engaged with. The reflection Brains' fleet never actually did.
triggers:
  - "weekly reflection"
  - "reflect on my week"
  - "what did I learn this week"
  - "weekly review"
  - "patterns this week"
tools:
  - query
  - search
  - think
  - list
  - get_page
  - put_page
mutating: true
---

# Weekly-Reflection Skill

Once a week, step back from the daily feed noise and write down what the week
*meant*: the patterns, the shifts, what held up, what to watch. Output is a
durable `reflections/<iso-week>.md` page the user (and future skills) can read.

This is the self-reflection the old Brains fleet promised but never delivered —
here it's a thick skill the dream cycle + skillopt keep sharpening.

## Contract

- **Reflective, not a re-digest.** Don't restate the daily digests — extract the
  *week-level* signal: what recurred, what changed, what resolved.
- **Track conviction over time.** Where a feed source's call shifted across the
  week (watch → conviction, bullish → fading), say so with dates.
- **Did calls play out?** Where the week's price action or news confirmed or
  broke a call, note it — this is how the brain learns which sources have edge
  (NOT a trading signal; an accuracy memory).
- **Attribute** every observation to source pages.
- **Honest gaps.** If the week was thin, say so; don't manufacture insight.
- **Writes exactly one page** (`reflections/<iso-week>`); never mutates feed pages.

## Phases

1. **Gather the week.** Pull the week's material across feeds + any decisions:

   ```bash
   gbrain list --tag finance -n 200          # this week's feed pages
   gbrain query "this week's recurring tickers, themes, conviction shifts, calls that played out" --rerank
   ```

2. **Reflect** with `think` (deep tier → Claude via the Max bridge):

   ```bash
   gbrain think "Reflect on the brain's last 7 days across my fintwit X, YouTube, and Discord feeds. Write a WEEK-LEVEL reflection, not a re-digest: (1) recurring themes/tickers and how attention shifted day to day; (2) conviction changes by source (who moved from watch→conviction or reversed); (3) calls that visibly played out or broke this week; (4) what I engaged with or flagged; (5) what to watch next week. Attribute every point. Be honest if the week was thin."
   ```

3. **Persist** the reflection as a page — either the `put_page` op (Hermes), or
   write a `reflections/<iso-week>.md` file (frontmatter `type: reflection`,
   `week`, `created`, `tags: [reflection, finance]`) into the brains-ingest
   source and `gbrain import` it (what the weekly cron does — CLI `gbrain put`
   is unreliable for new slugs).

4. **Surface briefly.** Optionally post a 3-5 line summary to the user's channel;
   the full reflection lives at the page.

## Self-tuning (skillopt)

Tunable: what counts as week-level signal vs daily noise, and the
conviction-tracking heuristic — skillopt learns from which reflections the user
revisits. Fixed: attribution, the accuracy-memory framing (track whether calls
played out), and "reflect, don't re-digest".
