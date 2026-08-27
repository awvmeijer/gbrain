---
name: what-needs-me
description: Surface what actually needs the user's attention or a decision right now — pending approvals, open decisions, follow-ups, and feed signals that warrant a call. Ranked, read-only, never a generic digest.
triggers:
  - "what needs me"
  - "what needs my attention"
  - "anything need a decision"
  - "open loops"
  - "what should I act on"
tools:
  - query
  - search
  - think
  - list_pages
  - get_page
mutating: false
---

# What-Needs-Me Skill

Answer one question: **what should the user act on or decide, right now?** This is
NOT the daily feed digest (that's `fintwit-analyst`) and NOT a status dump — only
items that need a human.

## Contract

- **Actionable only.** Every line is something to decide, approve, reply to, or
  follow up on. If it's just informational, it does not belong here.
- **Each item states WHY it needs you** and a **suggested next step**.
- **Pending approvals lead** — see `skills/conventions/proposals.md`.
- **Attribution**: link the source page/proposal for every item.
- **Ranked** by urgency × importance; cap at the ~10 that matter.
- **Read-only.** Surfacing only — never act, never flip a proposal's status.
  (To act, a skill writes a proposal page; to decide, the human flips status.)

## Phases

1. **Pending approvals.** List proposal pages awaiting a decision:

   ```bash
   gbrain list --type proposal              # then filter status: pending
   ```

   For each: the summary, `rationale`, and `rollback` so it's decidable at a glance.

2. **Open decisions & follow-ups.** Pull recent material where the user (or a
   source) left something open — questions asked of the brain, conflicting calls
   from the feeds (`fintwit-analyst` flags these), commitments/“I’ll look into X”.

   ```bash
   gbrain query "open question, decision needed, follow up, conflicting calls, to-do"
   ```

3. **Synthesize & rank** with `think` (deep tier → Claude via the Max bridge):

   ```bash
   gbrain think "From the brain's pending proposals + recent open loops, list ONLY what needs my attention or a decision now. For each: one line on what it is, WHY it needs me, and the suggested next step. Rank by urgency × importance. Attribute every item to its page. Skip anything purely informational."
   ```

4. **Shape the output** per the Output Format below.

## Output Format

Three sections, ranked within each by urgency × importance, ~10 items total:

- **Decide / approve** — pending proposals + forks where the user must pick.
  Each line: what it is → why it needs you → suggested next step → source page.
- **Reply / follow up** — threads or commitments awaiting the user.
- **Worth a look** — high-signal items that may need action soon (clearly
  marked as not-yet-actionable).

If nothing qualifies: say so plainly ("Nothing needs you right now.") — an
empty result is a valid, honest answer.

## Anti-Patterns

- **Digest creep.** Informational items belong in `fintwit-analyst`, not here —
  if there's no decision or action, it's out.
- **Acting instead of surfacing.** Never flip a proposal's status or perform
  the follow-up; this skill only points.
- **Un-attributed items.** Every line links its page/proposal or it's dropped.
- **Padding an empty day.** Manufacturing "attention items" to avoid an empty
  answer trains the user to ignore the skill.
- **Unbounded lists.** More than ~10 items means the ranking failed — cut,
  don't scroll.

## Self-tuning (skillopt)

Tunable: the ranking heuristic and what counts as "actionable" vs noise — let
skillopt learn from which items the user actually acts on. Fixed: read-only,
attribution, and the lead-with-pending-approvals contract.
