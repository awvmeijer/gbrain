"""Email triage → proposal pages (the recipe's classify-and-propose half).

Ported from the archived brain's `brain/agents/triage.py`, reshaped for the
proposals-as-pages gate (skills/conventions/proposals.md): verdicts become
`proposals/<date>-email-*.md` pages under the ingest dir, surfaced by
`what-needs-me` / `/api/needs`, decided by the human via `/api/decide` (or by
editing `status:`). NOTHING touches Gmail at proposal time — unlike the old
pipeline, no drafts are created and no labels move; executing an approved
proposal is a separate explicit step (a future executor, like trello-executor).

Old gateway verdict → new proposal page:
    archive        → action: archive_email       (kind: action)
    draft_reply    → action: send_drafted_reply  (kind: draft; draft text in the page body)
    flag_attention → action: read_yourself       (kind: finding; no mailbox mutation)
    skip           → no page

Five-field discipline carried over (old gateway → page frontmatter):
    agent → proposed_by · action → action · target → target ·
    rationale → rationale · rollback_note → rollback
All five are required; a verdict that can't fill them gets defaults or is dropped.

Drafts-on-approval (Phase 3): each run FIRST executes approved `draft_reply`
proposals — a proposal page with `status: approved` + `action:
send_drafted_reply` and no `draft_id` yet gets its draft text created as a
real Gmail DRAFT (never sent; sending stays a human act in Gmail, or a future
send executor). The page is then stamped `draft_id` + `drafted_at` — on BOTH
the brain page and the ingest file, because `gbrain import` short-circuits on
`existing.content_hash == file hash`: if only the brain page changed, the
next nightly import would re-import the stale file and clobber the stamp
(→ duplicate drafts). Approvals arrive via the dashboard or the Telegram
approvals bot (`/api/decide`). Disable with `--no-execute-drafts`.

Cursor semantics (same as old ingest_state): `~/.gbrain/email-triage-state.json`
stores the newest Gmail internalDate (ms) already classified; threads at or
below it are skipped, so re-runs no-op until new mail arrives. Belt-and-braces
dedup: one proposal per thread id — an existing `*-<thread_id>*.md` page under
proposals/ skips the thread even if the cursor was reset.

Classification: the Max bridge's OpenAI-compatible endpoint
(http://127.0.0.1:8789/v1, model claude-sonnet — same as youtube-to-brain's
--summarize). "Temporarily unavailable" (connect errors, timeouts, 5xx/429) →
wait ~75 s and retry, twice; an explicit denial (other 4xx) is noted and the
batch is dropped WITHOUT advancing the cursor, so the next run retries.

`--dry-run` swaps in a deterministic stub classifier (no bridge, no LLM) and
never writes state; pair with `--fixtures <dir>` to also skip Gmail entirely:

    python triage.py /tmp/out --dry-run --fixtures fixtures/

Usage:
    python triage.py <output_dir> [--query Q] [--max-threads 25]
                     [--bridge http://127.0.0.1:8789] [--model claude-sonnet]
                     [--dry-run] [--fixtures DIR]
    python triage.py --auth        # USER-RUN ONLY: interactive OAuth re-auth
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gmail_client

HERE = Path(__file__).parent
GBRAIN_DIR = Path.home() / "gbrain"
STATE_PATH = Path.home() / ".gbrain" / "email-triage-state.json"
PROMPT_PATH = HERE / "prompt.md"

AGENT = "email-triage"
DEFAULT_QUERY = "is:unread -category:promotions -category:social"

# Cap how much of each email body the classifier sees per thread, so a
# 25-thread batch stays well under any sane prompt budget.
MAX_BODY_CHARS = 1500

# Old gateway verdict → proposal-page action/kind (see module docstring).
ACTION_MAP = {
    "archive": ("archive_email", "action"),
    "draft_reply": ("send_drafted_reply", "draft"),
    "flag_attention": ("read_yourself", "finding"),
}

BRIDGE_RETRIES = 2  # extra attempts after the first
BRIDGE_RETRY_SLEEP_S = 75  # per task rule: wait 60-90 s on "temporarily unavailable"


@dataclass
class Verdict:
    thread_id: str
    action: str
    rationale: str
    rollback_note: str
    draft: dict[str, Any] | None


# ---------------------------------------------------------------- state


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:  # noqa: BLE001 — first run / corrupt file
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------- classify


def _format_thread(t: Any) -> str:
    body = (t.body_text or "").strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "…[truncated]"
    return (
        f"=== thread_id: {t.thread_id} ===\n"
        f"From: {t.sender}\n"
        f"To: {t.to}\n"
        f"Date: {t.date_header}\n"
        f"Subject: {t.subject}\n"
        f"Labels: {', '.join(t.label_ids)}\n"
        f"Messages in thread: {t.message_count}\n"
        f"---\n"
        f"{body}\n"
    )


def _parse_ndjson(text: str) -> list[dict[str, Any]]:
    """Pull JSON objects from the model's output, one per line — fence-tolerant."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    objs: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or not line.startswith("{"):
            continue
        try:
            objs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return objs


def _bridge_complete(system: str, user: str, *, bridge: str, model: str) -> str | None:
    """One classification call via the Max bridge; retry transient failures.

    Returns the raw text, or None on explicit denial / exhausted retries —
    the caller drops the batch without advancing the cursor.
    """
    import httpx

    for attempt in range(1 + BRIDGE_RETRIES):
        try:
            r = httpx.post(
                f"{bridge}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=180,
            )
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError("transient", request=r.request, response=r)
            if r.status_code >= 400:  # explicit denial — note and move on
                print(f"bridge DENIED ({r.status_code}): {r.text[:200]}", file=sys.stderr)
                return None
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 — connect error / timeout / 5xx
            if attempt >= BRIDGE_RETRIES:
                print(f"bridge unavailable after {attempt + 1} attempts: {type(e).__name__}", file=sys.stderr)
                return None
            print(
                f"bridge temporarily unavailable ({type(e).__name__}); "
                f"retrying in {BRIDGE_RETRY_SLEEP_S}s…",
                file=sys.stderr,
            )
            time.sleep(BRIDGE_RETRY_SLEEP_S)
    return None


def classify(threads: list[Any], *, bridge: str, model: str) -> list[Verdict]:
    if not threads:
        return []
    system = PROMPT_PATH.read_text()
    user = "\n".join(_format_thread(t) for t in threads)
    raw = _bridge_complete(system, user, bridge=bridge, model=model)
    if raw is None:
        return []
    by_id = {t.thread_id: t for t in threads}
    verdicts: list[Verdict] = []
    for obj in _parse_ndjson(raw):
        tid = str(obj.get("thread_id") or "")
        if tid not in by_id:
            continue
        verdicts.append(
            Verdict(
                thread_id=tid,
                action=str(obj.get("action") or "skip"),
                rationale=str(obj.get("rationale") or "")[:500],
                rollback_note=str(obj.get("rollback_note") or "")[:500],
                draft=obj.get("draft") if isinstance(obj.get("draft"), dict) else None,
            )
        )
    return verdicts


# Deterministic stub for --dry-run: no bridge, no LLM, same Verdict shape.
_STUB_NOISE = ("noreply", "no-reply", "donotreply", "notifications@", "newsletter", "mailer-daemon", "marketing@")
_STUB_ATTENTION = ("security alert", "sign-in", "invoice", "statement", "legal", "contract")


def classify_stub(threads: list[Any]) -> list[Verdict]:
    out: list[Verdict] = []
    for t in threads:
        sender = (t.sender or "").lower()
        text = f"{t.subject or ''} {t.body_text or ''}".lower()
        if any(p in sender for p in _STUB_NOISE):
            out.append(Verdict(t.thread_id, "archive", "STUB: automated/no-reply sender.", "Move thread back to inbox.", None))
        elif any(p in text for p in _STUB_ATTENTION):
            out.append(Verdict(t.thread_id, "flag_attention", "STUB: matches attention keywords.", "No-op: nothing was done to the mailbox.", None))
        elif "?" in (t.body_text or ""):
            out.append(
                Verdict(
                    t.thread_id,
                    "draft_reply",
                    "STUB: sender asks a direct question.",
                    "Discard the draft; do not send.",
                    {"to": t.sender, "subject": f"Re: {t.subject}", "body": "STUB DRAFT — replace before approving."},
                )
            )
        else:
            out.append(Verdict(t.thread_id, "skip", "STUB: no rule matched.", "", None))
    return out


# ---------------------------------------------------------------- propose


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:n].strip("-") or "email"


def _yaml_str(s: str) -> str:
    """Single-line, double-quoted YAML scalar — /api/needs parses `^key: (.+)$`."""
    s = re.sub(r"\s+", " ", (s or "")).strip().replace('"', "'")
    return f'"{s}"'


def _gmail_link(thread_id: str) -> str:
    # Generated by CODE, never by the LLM (link reliability rule from the
    # upstream email-to-brain recipe doc).
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def write_proposal(out_root: Path, t: Any, v: Verdict) -> Path | None:
    action, kind = ACTION_MAP[v.action]
    target = f"gmail:thread/{t.thread_id}"
    rationale = v.rationale or {
        "archive": "Auto-archive candidate.",
        "draft_reply": "Drafted reply for review.",
        "flag_attention": "Important — needs human read.",
    }[v.action]
    rollback = v.rollback_note or {
        "archive": "Re-add INBOX label to the thread (Gmail: Move to Inbox).",
        "draft_reply": "Discard the drafted reply; do not send.",
        "flag_attention": "No-op: this proposal does not modify the mailbox.",
    }[v.action]
    # Five-field discipline: refuse to write a page missing any of them.
    if not all([AGENT, action, target, rationale, rollback]):
        print(f"  drop {t.thread_id}: missing required proposal field", file=sys.stderr)
        return None

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prop_dir = out_root / "proposals"
    # Belt-and-braces dedup: one proposal per thread, ever.
    if list(prop_dir.glob(f"*-{t.thread_id}-*.md")):
        return None

    title = {
        "archive": f"Archive: {t.subject or t.thread_id}",
        "draft_reply": f"Send drafted reply: {t.subject or t.thread_id}",
        "flag_attention": f"Read yourself: {t.subject or t.thread_id}",
    }[v.action]
    safe_title = re.sub(r"\s+", " ", title).strip().replace('"', "'")

    lines = [
        "---",
        f"title: {_yaml_str(safe_title)}",
        "type: proposal",
        "status: pending",
        f"action: {action}",
        f"target: {target}",
        f"rationale: {_yaml_str(rationale)}",
        f"rollback: {_yaml_str(rollback)}",
        f"proposed_by: {AGENT}",
        f"kind: {kind}",
        f"created: {day}",
        "source: gmail",
        f"thread_id: {t.thread_id}",
        f"subject: {_yaml_str(t.subject or '')}",
        f"from: {_yaml_str(t.sender or '')}",
        "tags: [email, triage, proposal]",
        "---",
        "",
        f"# {safe_title}",
        "",
        f"- **From:** {t.sender}",
        f"- **Date:** {t.date_header}",
        f"- **Thread:** [Open in Gmail]({_gmail_link(t.thread_id)})",
        f"- **Approving means:** "
        + {
            "archive": "an executor removes the INBOX label (Gmail archive).",
            "draft_reply": "an executor creates the draft below in Gmail and sends it as-is.",
            "flag_attention": "you acknowledge it — no mailbox mutation ever happens.",
        }[v.action],
        "",
        "## Why",
        "",
        rationale,
        "",
    ]
    if v.action == "draft_reply" and v.draft:
        lines += [
            "## Draft reply",
            "",
            f"- **To:** {v.draft.get('to') or t.sender}",
            f"- **Subject:** {v.draft.get('subject') or 'Re: ' + (t.subject or '')}",
            "",
            "```",
            str(v.draft.get("body") or "").strip(),
            "```",
            "",
        ]
    snippet = re.sub(r"\s+", " ", (t.snippet or t.body_text or "")).strip()[:400]
    lines += ["## Snippet", "", f"> {snippet}", "", "## Rollback", "", rollback, ""]

    path = prop_dir / f"{day}-email-{v.action.replace('_', '-')}-{t.thread_id}-{_slug(t.subject)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return path


# ------------------------------------------------- execute approved drafts


def _gb(args: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    """Sanctioned gbrain CLI call (same pattern as recipes/entity-extract)."""
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN_DIR), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _fm_field(content: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", content, re.MULTILINE)
    return m.group(1).strip().strip("'\"") if m else ""


def _parse_draft_section(content: str) -> dict[str, str] | None:
    """Pull to/subject/body out of the page's `## Draft reply` section."""
    sec = re.search(r"## Draft reply\n(.*?)(?:\n## |\Z)", content, re.DOTALL)
    if not sec:
        return None
    block = sec.group(1)
    to = re.search(r"\*\*To:\*\*\s*(.+)", block)
    subject = re.search(r"\*\*Subject:\*\*\s*(.+)", block)
    body = re.search(r"```\n(.*?)\n?```", block, re.DOTALL)
    if not (to and body and body.group(1).strip()):
        return None
    return {
        "to": to.group(1).strip(),
        "subject": subject.group(1).strip() if subject else "",
        "body": body.group(1).strip(),
    }


def _stamp_drafted(slug: str, content: str, draft_id: str, out_root: Path) -> None:
    """Record draft_id/drafted_at on the brain page AND the ingest file.

    The file write-through matters: `gbrain import` skips a file only when its
    hash matches the LIVE page's content_hash. A brain-only stamp would make
    the next nightly import re-import the stale file, wipe the stamp, and the
    run after that would create a duplicate Gmail draft.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    content = re.sub(r"^draft(_id|ed_at):\s*.+\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^(---\n)", rf"\1draft_id: {draft_id}\ndrafted_at: {now}\n", content, count=1)
    rc, out = _gb(["put", slug, "--content", content])
    if rc != 0:
        print(f"  stamp FAILED (brain) {slug}: {out.strip()[:120]}", file=sys.stderr)
    ing_file = (out_root / f"{slug}.md").resolve()
    if ing_file.is_relative_to(out_root.resolve()) and ing_file.exists():
        ing_file.write_text(content)


def execute_approved_drafts(out_root: Path) -> int:
    """Approved `send_drafted_reply` proposals → real Gmail drafts (NEVER sent).

    Idempotent via the `draft_id` stamp; safe to run every triage sweep.
    Returns the number of drafts created.
    """
    rc, out = _gb(["list", "--type", "proposal", "--limit", "100", "--sort", "updated_desc"])
    if rc != 0:
        print(f"execute-drafts: gbrain list failed: {out.strip()[:120]}", file=sys.stderr)
        return 0
    slugs = [ln.split("\t")[0].strip() for ln in out.splitlines()
             if ln.split("\t")[0].strip().startswith("proposals/")]
    created = 0
    for slug in slugs:
        rc, content = _gb(["get", slug])
        if rc != 0 or not content.strip():
            continue
        if (_fm_field(content, "proposed_by") != AGENT
                or _fm_field(content, "action") != "send_drafted_reply"
                or _fm_field(content, "status").lower() != "approved"
                or _fm_field(content, "draft_id")):
            continue
        thread_id = _fm_field(content, "thread_id")
        draft = _parse_draft_section(content)
        if not thread_id or not draft:
            print(f"  {slug}: approved but no parseable draft/thread_id — skipping", file=sys.stderr)
            continue
        try:
            draft_id = gmail_client.create_draft_reply(
                thread_id, to=draft["to"],
                subject=draft["subject"] or "Re:", body=draft["body"])
        except Exception as e:  # noqa: BLE001 — auth stale / API error: retry next run
            print(f"  {slug}: create_draft_reply failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        _stamp_drafted(slug, content, draft_id, out_root)
        created += 1
        print(f"  drafted {slug} → Gmail draft {draft_id} (NOT sent — send from Gmail)")
    if created:
        print(f"execute-drafts: {created} Gmail draft(s) created")
    return created


# ---------------------------------------------------------------- fixtures


def load_fixtures(fixtures_dir: Path) -> list[gmail_client.ThreadSummary]:
    out = []
    for p in sorted(fixtures_dir.glob("*.json")):
        d = json.loads(p.read_text())
        out.append(gmail_client.ThreadSummary(**d))
    return out


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output_dir", nargs="?", help="ingest dir (e.g. ~/brains-ingest)")
    ap.add_argument("--query", default=DEFAULT_QUERY)
    ap.add_argument("--max-threads", type=int, default=25)
    ap.add_argument("--bridge", default="http://127.0.0.1:8789", help="Max bridge base url")
    ap.add_argument("--model", default="claude-sonnet")
    ap.add_argument("--dry-run", action="store_true", help="stub classifier, no bridge, no state writes")
    ap.add_argument("--fixtures", help="dir of thread-*.json fixtures instead of Gmail")
    ap.add_argument("--auth", action="store_true", help="USER-RUN ONLY: interactive Gmail OAuth re-auth")
    ap.add_argument("--no-execute-drafts", action="store_true",
                    help="skip turning approved draft_reply proposals into Gmail drafts")
    args = ap.parse_args()

    if args.auth:
        # Interactive by definition — never reached from cron/agents.
        path = gmail_client.bootstrap_auth()
        print(f"token written to {path} (0600). Contents not shown.")
        return

    if not args.output_dir:
        ap.error("output_dir is required (or use --auth)")
    out_root = Path(args.output_dir).expanduser()

    # Approved drafts FIRST — independent of whether new mail arrived, and the
    # human-facing outcome of the last approval round. Never in offline modes.
    if not args.no_execute_drafts and not args.dry_run and not args.fixtures:
        try:
            execute_approved_drafts(out_root)
        except Exception as e:  # noqa: BLE001 — executor trouble must not block triage
            print(f"execute-drafts failed: {type(e).__name__}: {e}", file=sys.stderr)

    state = _load_state()
    cursor_ms = int(state.get("cursor_ms") or 0)

    # --- gather threads (fixtures or Gmail) ---
    if args.fixtures:
        threads = load_fixtures(Path(args.fixtures))
    else:
        try:
            ids = gmail_client.list_thread_ids(query=args.query, max_results=args.max_threads)
        except gmail_client.GmailError as e:
            print(f"gmail: {e}", file=sys.stderr)
            sys.exit(1)
        threads = []
        for tid in ids:
            try:
                threads.append(gmail_client.get_thread(tid))
            except Exception as e:  # noqa: BLE001 — skip an unreadable thread
                print(f"  thread {tid}: {type(e).__name__}", file=sys.stderr)

    threads = [t for t in threads if t.last_internal_date_ms > cursor_ms]
    if not threads:
        print("nothing new past cursor; no-op")
        if not args.dry_run:
            _save_state({**state, "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return

    # --- classify ---
    if args.dry_run:
        verdicts = classify_stub(threads)
    else:
        verdicts = classify(threads, bridge=args.bridge, model=args.model)
        if not verdicts:
            # Bridge down/denied or unparseable output: drop the batch WITHOUT
            # advancing the cursor so the next run retries these threads.
            print("no verdicts (bridge unavailable/denied?); cursor NOT advanced", file=sys.stderr)
            return

    # --- propose ---
    by_id = {t.thread_id: t for t in threads}
    by_action: dict[str, int] = {}
    written = 0
    new_cursor = cursor_ms
    for v in verdicts:
        t = by_id.get(v.thread_id)
        if not t:
            continue
        new_cursor = max(new_cursor, t.last_internal_date_ms)
        by_action[v.action] = by_action.get(v.action, 0) + 1
        if v.action not in ACTION_MAP:  # skip (or junk verdict) → no page
            continue
        path = write_proposal(out_root, t, v)
        if path:
            written += 1
            print(f"  + {path.relative_to(out_root)}")

    if not args.dry_run:
        _save_state(
            {
                "cursor_ms": new_cursor,
                "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
    print(
        f"threads: {len(threads)} | verdicts: {len(verdicts)} | proposals written: {written} "
        f"| by_action: {by_action} | cursor: {new_cursor}{' (dry-run, state untouched)' if args.dry_run else ''}"
    )


if __name__ == "__main__":
    main()
