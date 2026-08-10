---
name: group-watch
description: Track the research group — new papers BY Steve Yim's group (author pages) and new citations OF their work (cited_by edges). Surfaces cross-over signals where the group's work is picked up by an adjacent field. Research-domain sibling of the convergence engine.
triggers:
  - "group watch"
  - "what's new from the group"
  - "new papers from Steve Yim"
  - "who cited our work"
  - "research group update"
  - "citations of our papers"
tools:
  - query
  - search
  - think
  - list_pages
  - get_page
  - backlinks
mutating: false
---

# Group Watch Skill

Keep the user (a research scientist in Steve Yim's group — Centre for Climate
Change and Environmental Health, NTU; atmospheric science / air quality) on top of
**their own research neighbourhood**, on the same graph the finance side uses.

The `science-to-brain` recipe has already written the substrate:
- `author/steve-hung-lam-yim` (+ co-authors) — `type: author`, `openalex_id`.
- `papers/oa-*` and `papers/arxiv-*` — `type: paper`.
- `paper --authored_by--> author` — the group's output.
- `paper --cited_by--> paper` — **who recently cited the group's work** (the edge).

## Contract

- **Every item is a real page** — cite the slug (`papers/oa-…`, `author/…`). No
  invented papers; if a claim isn't backed by a page, say so.
- **Lead with the cross-over signal.** The highest-value item is a *new citation of
  the group's work by someone in an adjacent field* — the research analogue of the
  "1+1=3" convergence (a paper you built on was just picked up elsewhere). Pull
  these from `cited_by` edges on the group's recent papers.
- **Then new output** — papers `authored_by` the group since the last run, newest
  first, with venue + date.
- **Volume is never the signal** (same house rule as fintwit): one citation from a
  genuinely adjacent group beats ten self-citations.

## Procedure

1. `backlinks author/steve-hung-lam-yim` → the group's papers (`authored_by`).
2. For each recent group paper, read its `cited_by` edges → citing papers; flag any
   whose authors/venue are OUTSIDE the group's usual field (the cross-over).
3. `think` a short synthesis: what's new, who's citing us, and any cross-field
   pickup worth a closer look — cite every page.
4. Surface as a digest section (or a `what-needs-me` proposal if action is implied,
   e.g. "reviewer request", "collaboration signal").
