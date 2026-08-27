---
name: field-weekly
description: Weekly read of the field — recent arXiv papers in the group's categories (physics.ao-ph etc.), clustered by theme, with what's relevant to the user's work (air quality, atmospheric modelling, 3DREAMS@SG). The research-domain field-scan.
triggers:
  - "field weekly"
  - "what's new in the field"
  - "recent arxiv in my area"
  - "atmospheric science weekly"
  - "weekly field scan"
tools:
  - query
  - search
  - think
  - list_pages
  - get_page
mutating: false
---

# Field Weekly Skill

A weekly scan of the **field** (not just the group): recent `type: paper`,
`source: arxiv` pages in the group's categories (`physics.ao-ph` and any added to
the `science-to-brain` recipe). The reader is a working atmospheric scientist —
give them the handful of papers worth opening, not a feed.

## Contract

- **Cite every paper** by slug (`papers/arxiv-…`) + title + date. No un-sourced
  summaries.
- **Cluster by theme**, not by date — group the week's papers into 3–5 themes
  (e.g. "PM2.5 exposure modelling", "satellite retrievals", "transport/dispersion",
  "health impacts"), lead with the theme most relevant to the user's work
  (air-quality / exposure / **3DREAMS@SG** platform).
- **Flag the cross-over** with the group's own work when it exists — a field paper
  that cites or builds on a `paper --authored_by--> author/steve-…` page is the one
  to read first (reuse the `group-watch` graph).

## Procedure

1. `list_pages type:paper source:arxiv` (recent) → the week's field papers.
2. Cluster by theme (`think` over titles/abstracts); rank themes by relevance to the
   user's air-quality / exposure work.
3. For each theme, name the 1–2 papers worth opening + one line why.
4. `think` a short "what to read this week" synthesis, cite every page. Optionally
   emit a digest section alongside the finance digest — same front door.

## Output Format

- **Read this week** — the 1–3 papers worth opening across all themes, each:
  title, `papers/arxiv-…` slug, date, one line on why it matters to the user's
  work. Group cross-overs (papers touching the group's own work) first.
- **By theme** — 3–5 themes, most-relevant first; per theme, the 1–2 notable
  papers with a one-line takeaway each.
- **Thin week?** Say so — "quiet week in <category>" beats a stretched list.

## Anti-Patterns

- **Feed dump.** Listing every paper defeats the skill — the deliverable is the
  handful worth opening.
- **Un-cited summaries.** Every claim carries its `papers/…` slug; no summary
  from memory of the field.
- **Date-ordered walls.** Clustering by theme is the contract; a chronological
  list is a bug.
- **Relevance drift.** Ranking by general impressiveness instead of relevance
  to air-quality / exposure / 3DREAMS@SG work.
- **Manufacturing significance** in a thin week instead of saying it was quiet.
