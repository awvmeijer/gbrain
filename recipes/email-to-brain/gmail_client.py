"""Gmail API wrapper for the email-to-brain recipe (deterministic data layer).

Ported from the archived brain's `brain/integrations/gmail.py`. Read-heavy by
design: triage only LISTS and GETS threads. The mutation helpers (`archive`,
`modify_labels`, `create_draft_reply`, `send_draft`) are ported too, but they
are for a future *executor* that acts on `status: approved` proposal pages —
`triage.py` never calls them (proposals-as-pages gate,
skills/conventions/proposals.md).

Auth model (files, not Keychain — OAuth tokens are Google-format JSON blobs):
- Canonical copies live under `~/.gbrain/` with 0600 perms:
    ~/.gbrain/google_oauth_client.json   (the "Desktop app" OAuth client)
    ~/.gbrain/gmail_token.json           (the user's access/refresh token)
- The archived brain's copies at `~/brain/data/{google_oauth_client,gmail_token}.json`
  are treated READ-ONLY: if the new path is missing we provision a 0600 copy
  from the legacy path once. Refreshed tokens are written ONLY to the new path;
  the legacy files are never touched.
- `bootstrap_auth()` runs the interactive InstalledAppFlow (browser consent).
  It is only reachable via `triage.py --auth`, which the USER runs — agents and
  cron must never trigger it (Gmail has been frozen since 2026-05-06; the
  refresh token has likely been expired/revoked, so headless refresh will fail
  until the user re-auths).

Scope: `gmail.modify` — read messages + add/remove labels + create AND send
drafts (Google's scope table covers `drafts.send` under `gmail.modify`).

Google client libs are imported lazily inside functions so `--dry-run
--fixtures` and `py_compile` work without them installed. Install (recipe-local
venv, mirroring youtube-to-brain):
    python3 -m venv recipes/email-to-brain/.venv
    recipes/email-to-brain/.venv/bin/pip install \
        google-api-python-client google-auth google-auth-oauthlib httpx
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

HOME = Path.home()
CLIENT_PATH = HOME / ".gbrain" / "google_oauth_client.json"
TOKEN_PATH = HOME / ".gbrain" / "gmail_token.json"
# Archived brain's copies — read-only fallbacks, never written.
LEGACY_CLIENT_PATH = HOME / "brain" / "data" / "google_oauth_client.json"
LEGACY_TOKEN_PATH = HOME / "brain" / "data" / "gmail_token.json"


class GmailError(RuntimeError):
    pass


@dataclass
class ThreadSummary:
    thread_id: str
    snippet: str
    subject: str
    sender: str
    to: str
    date_header: str
    label_ids: list[str]
    message_count: int
    last_internal_date_ms: int
    body_text: str  # last message in the thread, decoded


def _write_0600(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)


def _provision_from_legacy(new: Path, legacy: Path) -> None:
    """One-time 0600 copy legacy → ~/.gbrain/ if the new path is missing.

    The legacy file is only READ; all future writes (refresh, re-auth) go to
    the new path. Contents are never printed."""
    if new.exists() or not legacy.exists():
        return
    _write_0600(new, legacy.read_text())


def _load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    _provision_from_legacy(TOKEN_PATH, LEGACY_TOKEN_PATH)
    if not TOKEN_PATH.exists():
        raise GmailError(
            f"no gmail token at {TOKEN_PATH} (and no legacy copy at "
            f"{LEGACY_TOKEN_PATH}). Run `triage.py --auth` (user-run, opens a "
            "browser) first."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:  # noqa: BLE001 — stale/revoked refresh token
            raise GmailError(
                "token refresh failed (Gmail has been frozen since 2026-05-06, "
                "so the refresh token is likely stale/revoked). Re-auth with "
                f"`triage.py --auth`. Underlying error: {type(e).__name__}"
            ) from e
        _write_0600(TOKEN_PATH, creds.to_json())
    return creds


def _service():
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=_load_credentials(), cache_discovery=False)


def auth_ok() -> bool:
    try:
        _load_credentials()
        return True
    except Exception:  # noqa: BLE001
        return False


def bootstrap_auth(*, port: int = 0) -> Path:
    """Interactive InstalledAppFlow → writes ~/.gbrain/gmail_token.json (0600).

    USER-RUN ONLY (via `triage.py --auth`): opens a browser, requires a human
    to sign in and grant gmail.modify. Never call from cron or an agent.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    _provision_from_legacy(CLIENT_PATH, LEGACY_CLIENT_PATH)
    if not CLIENT_PATH.exists():
        raise GmailError(
            f"missing OAuth client file at {CLIENT_PATH}. Create a 'Desktop app' "
            "OAuth client at console.cloud.google.com → APIs & Services → "
            "Credentials, then save the JSON there (0600)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_PATH), SCOPES)
    creds = flow.run_local_server(port=port)
    _write_0600(TOKEN_PATH, creds.to_json())
    return TOKEN_PATH


def _decode_body(payload: dict) -> str:
    """Pull text/plain (or text/html stripped) out of a message payload."""

    def walk(p: dict) -> str | None:
        mime = p.get("mimeType", "")
        body = p.get("body", {}) or {}
        data = body.get("data")
        if data and mime == "text/plain":
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
        for child in p.get("parts", []) or []:
            r = walk(child)
            if r:
                return r
        # Fallback to text/html if no plain.
        if data and mime == "text/html":
            html = base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
            # Cheap strip — this feeds a classifier, not a renderer.
            import re

            return re.sub(r"<[^>]+>", " ", html)
        return None

    return walk(payload) or ""


def _header(headers: list[dict], name: str) -> str:
    n = name.lower()
    for h in headers or []:
        if h.get("name", "").lower() == n:
            return h.get("value", "") or ""
    return ""


def list_thread_ids(
    *,
    query: str = "is:unread -category:promotions -category:social",
    max_results: int = 25,
) -> list[str]:
    svc = _service()
    out: list[str] = []
    page_token: str | None = None
    while True:
        resp = (
            svc.users()
            .threads()
            .list(
                userId="me",
                q=query,
                maxResults=min(max_results - len(out), 100),
                pageToken=page_token,
            )
            .execute()
        )
        for t in resp.get("threads", []) or []:
            out.append(t["id"])
            if len(out) >= max_results:
                return out
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


def get_thread(thread_id: str) -> ThreadSummary:
    svc = _service()
    t = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
    msgs = t.get("messages", []) or []
    if not msgs:
        raise GmailError(f"thread {thread_id} has no messages")
    last = msgs[-1]
    headers = (last.get("payload") or {}).get("headers", []) or []
    body = _decode_body(last.get("payload") or {})
    label_ids: list[str] = []
    for m in msgs:
        for lid in m.get("labelIds", []) or []:
            if lid not in label_ids:
                label_ids.append(lid)
    return ThreadSummary(
        thread_id=t["id"],
        snippet=(t.get("snippet") or "").strip(),
        subject=_header(headers, "Subject"),
        sender=_header(headers, "From"),
        to=_header(headers, "To"),
        date_header=_header(headers, "Date"),
        label_ids=label_ids,
        message_count=len(msgs),
        last_internal_date_ms=int(last.get("internalDate") or 0),
        body_text=body,
    )


# ---- Mutation helpers: FOR A FUTURE EXECUTOR ONLY (approved proposals) ----


def modify_labels(thread_id: str, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> None:
    svc = _service()
    svc.users().threads().modify(
        userId="me",
        id=thread_id,
        body={"addLabelIds": list(add), "removeLabelIds": list(remove)},
    ).execute()


def archive(thread_id: str) -> None:
    """Remove INBOX label — Gmail's 'archive' action."""
    modify_labels(thread_id, remove=["INBOX"])


def create_draft_reply(thread_id: str, *, to: str, subject: str, body: str) -> str:
    """Create a draft reply on a thread. Returns draft id."""
    from email.mime.text import MIMEText

    svc = _service()
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    res = (
        svc.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw, "threadId": thread_id}})
        .execute()
    )
    return res.get("id", "")


def send_draft(draft_id: str) -> str:
    """Send a previously-created draft. Returns the resulting message id."""
    if not draft_id:
        raise GmailError("send_draft: empty draft_id")
    svc = _service()
    try:
        res = svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    except Exception as e:  # noqa: BLE001
        raise GmailError(f"drafts.send failed: {e}") from e
    return res.get("id", "")
