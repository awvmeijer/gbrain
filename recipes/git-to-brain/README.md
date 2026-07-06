---
id: git-to-brain
name: Git-to-Brain (repo commit log)
version: 0.1.0
description: New commits across the configured repos → one digest page per repo per day. Stdlib + git, no keys, no network.
category: sense
requires: []
secrets: []
setup_time: 5 min
cost_estimate: "$0 (local git log — no API, no quota)"
---

# Git-to-Brain (repo commits)

Ports the archived `~/brain` app's git-commit ingestion (`brain/ingest/git.py` +
`config.toml [ingest.git.repos]`). For each repo in `repos.txt`
(`slug | path | remote`) it walks `git log <cursor>.. --all` — cursor = the
last-seen SHA, exactly the old brain's semantics — and digests the day's new
commits into **one page per repo per day**: sha-short, subject, author,
files-changed count per line. Same volume control as rss-to-brain's per-feed
digest pages; re-runs rewrite today's page with a superset (never duplicate,
never lose lines).

**Cursor init — nothing historical, ever.** All historical commits are already
in the corpus as `legacy-git_commit` pages (the 2026-07-02 legacy replay). On
the FIRST run for a repo the collector initializes the cursor to current HEAD
and writes **zero** pages; only commits made after that init flow in. A per-repo
`init_ts` also filters pre-init commits that `--all` can surface later (stale
unmerged branches are reachable-from-a-ref but never ancestors of the cursor),
and a seen-SHA set makes re-runs idempotent regardless of cursor state.

- **State**: `~/gbrain/logs/git-state.json` (rss/edgar/federal convention —
  never inside `~/brains-ingest`): per-repo cursor, init timestamp, seen SHAs,
  and recent-day commit snippets for superset rewrites.
- **Canonical**: the `remote` column of `repos.txt` (= `git remote get-url
  origin`, `.git` stripped, matching legacy page frontmatter); `-` for
  no-remote repos → the abs path is the canonical. Lands as `repo:` in each
  page's frontmatter, so git pages join the legacy slice on the same key.
- **No deps, no network**: stdlib + the `git` binary. `--fetch` opts into a
  best-effort `git fetch --all --prune` (the old brain fetched for the NTU SSH
  mirror; local-only is the default posture now).
- `~/brain` itself is archived — kept in `repos.txt` (history closed, catches
  any stray final commit) but expect no new pages from it. `~/gbrain` is the
  live repo and IS in the list.

## Run

```bash
~/gbrain/sidecars/.venv/bin/python recipes/git-to-brain/collect.py ~/brains-ingest --max 200
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
```

Wired into `infra/feeds-cron.sh` right after the rss collector. Edit
`repos.txt` (`slug | path | remote`) to add/remove repos — a new repo's first
run only initializes its cursor (zero pages), by design.

`--extra-repo "slug | path | remote"` adds a repo for ONE run without touching
`repos.txt` — test-only (used to verify the pipeline with a scratch repo).

## Notes / follow-ups

- **GitHub events (PRs / issues / reviews) — documented follow-up, not shipped.**
  The old `brain/ingest/github.py` also ingested the authed user's GitHub
  events feed (`gh api users/<me>/events`, cursor = max event id, source
  `github_activity`). Porting it faithfully isn't cheap: the events payload is
  slim (PR/issue titles need per-item REST hydration), events need mapping back
  to repo slugs, and it adds a network + `gh`-auth dependency to an otherwise
  offline collector. Ship shape when wanted: a second cursor block in
  `git-state.json` keyed `github:<login>`, filter to the 5 high-signal event
  types (PR, issue, issue-comment, review, review-comment) on configured
  remotes, and append `[gh]`-prefixed lines into the same per-repo day pages.
  Solo-dev reality check: most of that signal is your own PRs merging, which
  the commit log already carries.
- Commits are bucketed by **author date**; rebased/re-pushed commits authored
  before cursor-init are filtered by `init_ts` (their content is already in the
  legacy slice). Commits dated outside the 7-day state window bucket under
  today so every page the collector might rewrite stays rebuildable from state.
- If a cursor SHA disappears (history rewrite / gc), the collector re-initializes
  that repo at HEAD and says so — it never re-floods history.
