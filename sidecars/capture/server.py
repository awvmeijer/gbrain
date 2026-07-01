#!/usr/bin/env python3
"""Phone-capture receiver — text / image-OCR / voice-dictation → the brain.

A tiny auth'd HTTP endpoint that an iOS Shortcut POSTs to over the existing
Tailscale Funnel (public HTTPS → 127.0.0.1:8787). No iCloud, no file-sync: the
Shortcut does OCR / speech-to-text ON DEVICE and sends plain text (optionally
attaching the raw image/audio for archival). Each capture becomes a
brains-ingest markdown page, imported in the background so it's queryable within
seconds.

Auth: every POST must carry `X-Brain-Key: <CAPTURE_KEY>` (Keychain service
`brain`). The endpoint is public via Funnel, so the key is mandatory — but it
only *writes* captures (no read/delete), so a leaked key's blast radius is spam.

Run: sidecars/.venv/bin/uvicorn ... (see infra/launchd/com.brains.capture.plist)
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import keyring
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

SERVICE = "brain"
HOME = Path.home()
GBRAIN_DIR = HOME / "gbrain"
ING = HOME / "brains-ingest"
CAP_DIR = ING / "capture"
FILE_DIR = CAP_DIR / "files"
LOG = GBRAIN_DIR / "logs" / "capture.log"
MAX_BYTES = 25 * 1024 * 1024  # 25 MB per attachment

app = FastAPI(title="brains-capture")


def _key() -> str | None:
    return keyring.get_password(SERVICE, "CAPTURE_KEY")


def _require(x_brain_key: str | None) -> None:
    want = _key()
    if not want:
        raise HTTPException(500, "CAPTURE_KEY not configured on the server")
    if x_brain_key != want:
        raise HTTPException(401, "bad or missing X-Brain-Key")


def _slug(s: str, n: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (s[:n].strip("-")) or "capture"


def _write_page(text: str, via: str, file_rel: str | None) -> Path:
    now = datetime.now().astimezone()
    ts = now.strftime("%Y-%m-%dT%H%M%S")
    first = (text.strip().splitlines() or [""])[0][:60]
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    body = [
        "---",
        f"title: Capture — {first or ts}",
        "source: capture",
        f"via: {via}",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"captured_at: {now.isoformat(timespec='seconds')}",
        "tags: [capture]",
        "---",
        "",
        f"# Capture — {now.strftime('%Y-%m-%d %H:%M')} ({via})",
        "",
        text.strip() or "_(no text)_",
    ]
    if file_rel:
        body += ["", f"Attachment: `{file_rel}`"]
    path = CAP_DIR / f"{ts}-{_slug(first or via)}.md"
    path.write_text("\n".join(body) + "\n")
    return path


def _ingest_async() -> None:
    """Fire-and-forget import + embed so the capture is live in ~seconds.

    Detached; failures land in logs/capture.log. If the tooling isn't reachable
    in this env, the daily feeds-cron still imports brains-ingest — so a capture
    is never lost, just not instantly live."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = (
        'export PATH="$HOME/.bun/bin:/opt/homebrew/bin:$PATH"; '
        f'cd "{GBRAIN_DIR}" && gbrain import "{ING}" --no-embed && gbrain embed --stale'
    )
    subprocess.Popen(
        ["/bin/bash", "-lc", cmd],
        stdout=open(LOG, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


@app.get("/health")
def health():
    return {"ok": True, "key_configured": bool(_key())}


@app.post("/capture")
async def capture(request: Request, x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    ctype = request.headers.get("content-type", "")
    text = ""
    via = "phone"
    file_rel = None

    if ctype.startswith("application/json"):
        data = await request.json()
        text = (data.get("text") or "").strip()
        via = (data.get("source") or via).strip() or via
    else:  # multipart/form-data (text field + optional file)
        form = await request.form()
        text = (str(form.get("text") or "")).strip()
        via = (str(form.get("source") or via)).strip() or via
        up = form.get("file")
        if up is not None and hasattr(up, "filename") and up.filename:
            raw = await up.read()
            if len(raw) > MAX_BYTES:
                raise HTTPException(413, "attachment too large (max 25 MB)")
            FILE_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%dT%H%M%S")
            safe = _slug(Path(up.filename).stem) + Path(up.filename).suffix.lower()[:6]
            dst = FILE_DIR / f"{ts}-{safe}"
            dst.write_bytes(raw)
            file_rel = str(dst.relative_to(ING))

    if not text and not file_rel:
        raise HTTPException(400, "nothing to capture (empty text and no file)")

    page = _write_page(text, via, file_rel)
    _ingest_async()
    return JSONResponse({"ok": True, "page": page.name, "file": file_rel, "via": via})
