---
name: todo-suggester
description: Proactively propose todos WITH due dates — from filing reaction windows, paper/conference deadlines, meeting commitments, and stale what-needs-me loops. Every suggestion routes through the proposals-as-pages gate; on approval the trello-executor makes a Trello card.
triggers:
  - "suggest todos"
  - "what should I do this week"
  - "propose tasks"
  - "todo suggestions"
  - "plan my week"
  - "what's due"
tools:
  - query
  - search
  - think
  - list_pages
  - get_page
  - backlinks
mutating: true
---

# Todo Suggester Skill

Act like a proactive PA: look across the brain and **propose dated todos**, each as
a `type: proposal` page (`action: create_todo`) so it lands in the same approval
gate as everything else. Nothing is created in Trello until the user approves; on
approval the `trello-executor` recipe makes the card.

## Where todos come from (evidence-first — cite the page)

1. **Filing reaction windows** — a fresh material 8-K on a watchlist name
   (`type: filing`, item 1.01 / 2.01 / 3.02) → "review $SYM's <event> filing" due in
   1–2 trading days. Cite the filing page.
2. **Convergence findings** — an open `type: finding` (score ≥ threshold) → "decide
   position on $SYM (N-class convergence)" due soon. Cite the finding.
3. **Paper / conference deadlines** — from `field-weekly` / `group-watch`
   (`type: paper`, arXiv/OpenAlex) → "read <paper> for <project>" or a submission
   deadline. Cite the paper.
4. **Meeting commitments** — action items from `meeting-ingestion` / Granola notes.
5. **Stale loops** — a `what-needs-me` item untouched > N days → nudge with a due date.

## Contract (proposal shape — all fields required)

Emit one proposal page per suggestion:

```
type: proposal
action: create_todo
status: pending
title: "<imperative todo — e.g. 'Review $FCEL 8-K (data-center deal)'>"
due: YYYY-MM-DD
list: "<optional Trello list id; else the executor's default>"
target: "<the evidence page slug>"
rationale: "<why now, one line, cite the evidence page>"
rollback: "Archive the card; no external side effects until approved."
proposed_by: todo-suggester
```

- **Every todo cites its evidence page.** No free-floating "you should probably…".
- **Every todo has a `due`.** A todo without a date is a wish; the ICS feed +
  Trello card both need it. Default the due sensibly (filing → +2d, finding → +3d,
  paper deadline → the real date).
- **Never create the Trello card here** — only the proposal. The gate + executor
  own the side effect (propose → approve → act).

## Output Format

- One `type: proposal` page per suggestion, exactly the shape above (all fields
  required, `status: pending`, `proposed_by: todo-suggester`).
- Plus a short human summary listing the proposals just filed: `<title> — due
  <date> — evidence: <slug>`, grouped by source (filings, findings, papers,
  meetings, stale loops), so the user can approve from one glance.
- Nothing suggestable → say so; file zero proposals.

## Anti-Patterns

- **Direct side effects.** Creating a Trello card, editing a todo, or flipping
  a proposal status from this skill breaks the propose→approve→act gate.
- **Undated todos.** A suggestion without `due` is a wish, not a todo — derive
  the date or don't file it.
- **Evidence-free suggestions.** "You should probably review your positions"
  with no cited page is noise; every proposal names its `target`.
- **Re-proposing.** Check for an existing pending/approved proposal on the same
  target before filing — duplicates erode trust in the gate.
- **Volume.** Five dated, evidenced todos beat twenty maybes.
