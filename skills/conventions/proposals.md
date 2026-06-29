# Convention: proposals-as-pages (the approval gate)

GBrain has no human-approval queue. We add one **without new harness code** —
a proposal is just a typed page. Thin harness, thick skill.

## Rule

**A skill never performs an outward/irreversible action directly** (send an
email, post to a channel, place an order, delete). Instead it writes a
**proposal page**, and an *executor* skill runs it only after a human flips it
to `approved`.

## Page shape — `proposals/<yyyy-mm-dd>-<slug>.md`

```markdown
---
type: proposal
status: pending          # pending | approved | rejected | expired
action: send_reply       # machine verb the executor understands
target: gmail:thread/abc # what it acts on
rationale: "Why this is worth doing — the human reads this to decide."
rollback: "How to undo it if approved in error."
proposed_by: fintwit-analyst   # the skill that proposed it
created: 2026-06-30
---

# <one-line summary>

<details / draft content the action would use>
```

All five fields (`action`, `target`, `rationale`, `rollback`, `status`) are
**required** — if a skill can't fill them, it shouldn't propose. (This is the
one genuinely good idea carried over from the old Brains gateway: an approval
queue is only useful if every item is decidable at a glance.)

## Lifecycle

1. **Propose** — an acting skill writes the page with `status: pending`.
2. **Surface** — `skills/what-needs-me` lists `type:proposal status:pending`.
3. **Decide** — a human (or, later, Hermes / the console) sets `status:` to
   `approved` or `rejected` (edit the page → `put_page`). Stale pending
   proposals are swept to `expired` by the dream cycle / a maintenance skill.
4. **Execute** — an *executor* skill (future) picks up `status: approved`,
   performs `action` on `target`, then sets `status: executed`.

## Why pages, not a new op/table

Pages are GBrain's universal primitive — search, graph, timeline, and the
dream cycle all work on a proposal page for free, and there's zero new
TypeScript to maintain across upstream rebases. If Hermes later needs an
MCP-callable `propose`/`decide`, add a thin op then — but the data model stays
these pages.
