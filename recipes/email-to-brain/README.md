---
id: email-to-brain
name: Email-to-Brain (Gmail triage)
version: 0.1.0
description: Unread Gmail threads are classified (archive / draft_reply / flag_attention / skip) and become pending PROPOSAL pages behind the what-needs-me approval gate. No mailbox mutation until a human approves — and execution is a separate explicit step.
category: act
requires: []
secrets:
  - name: google_oauth_client.json
    description: Google "Desktop app" OAuth client JSON. File at ~/.gbrain/google_oauth_client.json (0600), NOT Keychain — Google tokens are JSON blobs the client lib reads/writes.
    where: console.cloud.google.com → APIs & Services → Credentials → OAuth client ID → Desktop app
  - name: gmail_token.json
    description: The user's Gmail access/refresh token (scope gmail.modify). File at ~/.gbrain/gmail_token.json (0600). Minted by `triage.py --auth` (user-run, interactive).
    where: run `.venv/bin/python triage.py --auth` from this directory
setup_time: 15 min (mostly the OAuth re-auth)
cost_estimate: "$0 (Gmail API free quota; classification via the Max bridge)"
---

# Email-to-Brain: Gmail triage behind the approval gate

Port of the archived brain's Stage-6 email triage
(`~/brain/brain/agents/triage.py` + `brain/integrations/gmail.py` +
`brain/skills/triage.md`) onto the proposals-as-pages gate
(`skills/conventions/proposals.md`). One run: pull unread threads, classify
each with Claude via the Max bridge, and write one **pending proposal page**
per actionable verdict into `~/brains-ingest/proposals/`. The
`what-needs-me` skill and the dashboard (`/api/needs` in
`sidecars/capture/server.py`) surface them; a human decides via `/api/decide`
or by flipping `status:` on the page.

**Key change from the old pipeline:** verdicts NEVER touch Gmail. The old
agent created Gmail drafts at proposal time; here even the draft text just
lives in the proposal page body. Executing an approved proposal (archive the
thread, create + send the draft) is a separate explicit step — a future
executor in the trello-executor mold, using the mutation helpers already
ported into `gmail_client.py` (`archive`, `create_draft_reply`, `send_draft`).

## Pattern (code for data, LLM for judgment)

- **Deterministic collector** (`gmail_client.py`): list + fetch unread
  threads via the Gmail API (scope pinned to `gmail.modify`), decode bodies,
  generate Gmail links in code. Cursor in
  `~/.gbrain/email-triage-state.json` stores the newest `internalDate` (ms)
  already classified — re-runs no-op until new mail arrives (same semantics
  as the old `ingest_state` cursor). Belt-and-braces: one proposal per
  thread id, ever.
- **LLM judgment** (`triage.py classify`): the ported triage prompt
  (`prompt.md`) via the Max bridge's OpenAI-compatible endpoint
  (`http://127.0.0.1:8789/v1`, model `claude-sonnet` — same call shape as
  youtube-to-brain's `--summarize`). NDJSON verdicts, fence-tolerant parsing.
  If the bridge is down/denies, the batch is dropped **without advancing the
  cursor**, so the next run retries.
- **Approval gate** (pages): every proposal carries the old gateway's
  five-field discipline in frontmatter — `proposed_by` (=agent), `action`,
  `target`, `rationale`, `rollback` (=rollback_note) — all required, plus
  `status: pending`, `kind`, `thread_id`. `/api/needs` parses exactly these.

## Verdict mapping (old gateway → proposal page)

| old verdict      | old proposal action  | old side effect at proposal time | new page `action`    | new `kind` | approving means (future executor)          |
|------------------|----------------------|----------------------------------|----------------------|------------|--------------------------------------------|
| `archive`        | `archive_email`      | none                             | `archive_email`      | `action`   | remove `INBOX` label                       |
| `draft_reply`    | `send_drafted_reply` | **Gmail draft created**          | `send_drafted_reply` | `draft`    | create the draft in Gmail, then send as-is |
| `flag_attention` | `read_yourself`      | none                             | `read_yourself`      | `finding`  | acknowledged; never mutates the mailbox    |
| `skip`           | (no proposal)        | none                             | (no page)            | —          | —                                          |

## Install (one-time)

The Google client libs are NOT in `sidecars/.venv` — this recipe uses its own
venv (mirrors youtube-to-brain):

```bash
cd ~/gbrain/recipes/email-to-brain
python3 -m venv .venv
.venv/bin/pip install google-api-python-client google-auth google-auth-oauthlib httpx
```

## Re-auth (USER-RUN — the only remaining step)

Gmail triage has been frozen since **2026-05-06**; the old refresh token at
`~/brain/data/gmail_token.json` is almost certainly stale/revoked (Google
expires unused Desktop-app refresh tokens, and "testing"-status OAuth apps
expire them after 7 days). The recipe reads the old files **read-only** and
provisions its own 0600 copies under `~/.gbrain/`; a refresh failure tells
you to re-auth. To re-auth (opens a browser, needs a human):

```bash
cd ~/gbrain/recipes/email-to-brain
# 1. If you still have the old OAuth client, this is auto-copied on first use.
#    Otherwise download a "Desktop app" OAuth client JSON from
#    console.cloud.google.com → APIs & Services → Credentials, and save it as:
#    ~/.gbrain/google_oauth_client.json   (chmod 600)
# 2. Interactive consent (scope: gmail.modify). Writes ~/.gbrain/gmail_token.json (0600):
.venv/bin/python triage.py --auth
# 3. Smoke-test a real run (read-only vs Gmail; proposals only):
.venv/bin/python triage.py ~/brains-ingest --max-threads 5
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
# 4. Only then load the cron:
cp ~/gbrain/infra/launchd/com.brains.email-triage.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.brains.email-triage.plist
```

Token files are never printed by any code path here.

## Run

```bash
# Manual triage sweep (Gmail → proposal pages → import):
bash run.sh

# Or piecewise:
.venv/bin/python triage.py ~/brains-ingest [--query "is:unread ..."] [--max-threads 25]
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale

# Surface + decide:
gbrain list --type proposal                       # or the what-needs-me skill
curl -s -H "X-Brain-Key: $KEY" http://127.0.0.1:8787/api/needs
curl -s -X POST -H "X-Brain-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"slug":"proposals/<slug>","decision":"approve"}' http://127.0.0.1:8787/api/decide
```

Scheduled: `infra/launchd/com.brains.email-triage.plist` (08:15 / 12:15 /
16:15 / 20:15 — the old `com.brain.triage` cadence plus an evening pass).
**Not loaded by default**; load it only after re-auth succeeds.

## Offline verification (no Gmail, no LLM)

`--dry-run` swaps in a deterministic stub classifier and never writes cursor
state; `--fixtures` replaces Gmail with local thread JSONs:

```bash
.venv/bin/python triage.py /tmp/triage-out --dry-run --fixtures fixtures/
# → 3 fixture threads → archive (newsletter), draft_reply (question),
#   flag_attention (security alert) proposal pages under /tmp/triage-out/proposals/
```

(Works with `sidecars/.venv/bin/python` too — the Google libs are imported
lazily, only on real Gmail runs.)

## Scope notes

- **Scope stays `gmail.modify`** (read + label + create/send drafts). Sends
  will still only ever happen from an executor acting on an APPROVED
  proposal — the agent never sends unsolicited.
- The old pipeline's `+tag` capture, NTU-origin hints, and Outlook provider
  were NOT ported — this recipe is Gmail-only triage. Add them later if the
  old workflows come back.
- Bridge failure policy: transient errors (connect/timeout/5xx/429) retry
  after ~75 s, twice; explicit denials are logged and the batch is dropped
  with the cursor untouched.
