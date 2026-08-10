#!/usr/bin/env python3
"""Phone-capture receiver — text / image / voice → the brain, no iCloud, no Shortcuts.

Serves a small mobile web app (GET /) that you open once on the phone and Add to
Home Screen. Type a note, tap-record a voice memo, or snap a photo — it POSTs to
this receiver over the existing Tailscale Funnel (public HTTPS → 127.0.0.1:8787).
The Mac does the heavy lifting locally: **Apple Vision OCR** on images and
**Whisper (MLX)** on audio, so voice/photos become searchable text. Each capture
becomes a brains-ingest page, imported in the background so it's queryable in
seconds.

Auth: every POST needs `X-Brain-Key: <CAPTURE_KEY>` (Keychain service `brain`).
The endpoint is public via Funnel, so the key is mandatory; it only *writes*
captures, so a leaked key's blast radius is spam.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# Whisper (and its ffmpeg decode step) + bun/gbrain need Homebrew on PATH.
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "") + f":{Path.home()}/.bun/bin"

import keyring
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

SERVICE = "brain"
HOME = Path.home()
GBRAIN_DIR = HOME / "gbrain"
ING = HOME / "brains-ingest"
CAP_DIR = ING / "capture"
FILE_DIR = CAP_DIR / "files"
LOG = GBRAIN_DIR / "logs" / "capture.log"
DASHBOARD_DIST = GBRAIN_DIR / "dashboard" / "dist"
DEDUP_FILE = GBRAIN_DIR / "logs" / "capture-dedup.json"  # outside brains-ingest, never imported
DEDUP_WINDOW_S = int(os.environ.get("CAPTURE_DEDUP_WINDOW_S", "600"))  # 10 min
MAX_BYTES = 40 * 1024 * 1024  # 40 MB per attachment
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-small-mlx")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp", ".gif", ".tiff"}
AUDIO_EXTS = {".m4a", ".mp4", ".webm", ".wav", ".mp3", ".ogg", ".aac", ".caf", ".flac"}

app = FastAPI(title="brains-capture")


KEY_FILE = HOME / ".gbrain" / "client.key"
_KEY_CACHE: str | None = None


def _key() -> str | None:
    # Resolve once, cheapest-first (env → key file → Keychain), then cache for
    # the process lifetime. A per-request Keychain read pops the macOS unlock
    # dialog whenever the login keychain is locked (post-sleep) — the Keychain
    # stays canonical, but only the first resolution may touch it.
    global _KEY_CACHE
    if _KEY_CACHE:
        return _KEY_CACHE
    key = os.environ.get("CAPTURE_KEY") or None
    if not key:
        try:
            key = KEY_FILE.read_text().strip() or None
        except Exception:
            key = None
    if not key:
        try:
            key = keyring.get_password(SERVICE, "CAPTURE_KEY")
        except Exception:
            key = None
        if key:
            # Self-provision the client key file so menubar/hooks never need
            # the Keychain (0600; capture-key blast radius is spam, see above).
            try:
                KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(key)
            except Exception as e:
                _log(f"client.key provision failed: {e}")
    _KEY_CACHE = key
    return key


def _require(x_brain_key: str | None) -> None:
    want = _key()
    if not want:
        raise HTTPException(500, "CAPTURE_KEY not configured on the server")
    if x_brain_key != want:
        raise HTTPException(401, "bad or missing X-Brain-Key")


def _slug(s: str, n: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (s[:n].strip("-")) or "capture"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")


# Secrets that clients/crons and this server's own request paths may need.
# Same trap as CAPTURE_KEY everywhere: interactive/per-request Keychain reads
# pop the unlock dialog or die -25320/-60008 when the login keychain is locked.
_SECRET_FILES = {
    "DISCORD_BOT_TOKEN": HOME / ".gbrain" / "discord_bot.token",
    "DISCORD_WEBHOOK_URL": HOME / ".gbrain" / "discord_webhook.url",
    "TRADINGVIEW_TOKEN": HOME / ".gbrain" / "tradingview.token",
}
_SECRET_CACHE: dict[str, str] = {}
DISCORD_TOKEN_FILE = _SECRET_FILES["DISCORD_BOT_TOKEN"]


def _secret(name: str) -> str | None:
    """Resolve a secret memory → env → 0600 file → Keychain (then cache).
    Request paths must call THIS, never keyring directly. A Keychain failure
    propagates (callers decide fail-open vs fail-closed); the file tier makes
    that effectively unreachable after first startup provisioning."""
    if name in _SECRET_CACHE:
        return _SECRET_CACHE[name]
    val = os.environ.get(name) or None
    dest = _SECRET_FILES.get(name)
    if not val and dest:
        try:
            val = dest.read_text().strip() or None
        except Exception:
            val = None
    if not val:
        val = keyring.get_password(SERVICE, name)  # may raise — see docstring
        if val and dest:
            _write_secret_file(dest, val)
    if val:
        _SECRET_CACHE[name] = val
    return val


def _write_secret_file(dest: Path, val: str) -> None:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(val)
    except Exception as e:  # noqa: BLE001
        _log(f"{dest.name} provision failed: {e}")


def _provision_secret_files() -> None:
    """Keychain → 0600 files, once at startup — same idiom as _key()'s client.key
    self-provisioning. The sidecar starts at login while the keychain is unlocked;
    short-lived launchd crons (feeds at 06:30) read only the files, so they never
    hit the post-sleep locked-keychain -25320. Keychain stays canonical."""
    for secret, dest in _SECRET_FILES.items():
        try:
            val = keyring.get_password(SERVICE, secret)
        except Exception:  # noqa: BLE001 — keychain locked; keep any existing file
            return
        if val:
            _write_secret_file(dest, val)


# Off the main thread: a keychain read under launchd can BLOCK on securityd
# (not just raise -25320/-60008), which would stop uvicorn from ever binding.
threading.Thread(target=_provision_secret_files, daemon=True).start()


def _dedup_load() -> dict:
    try:
        return json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
    except Exception:
        return {}


def _dedup_seen(key: str) -> str | None:
    """Page name if this exact content (image bytes / text) was captured within the window."""
    now = time.time()
    hit = _dedup_load().get(key)
    return hit[1] if (hit and now - hit[0] < DEDUP_WINDOW_S) else None


def _dedup_record(key: str, page_name: str) -> None:
    try:
        now = time.time()
        data = {k: v for k, v in _dedup_load().items() if now - v[0] < DEDUP_WINDOW_S}  # prune
        data[key] = [now, page_name]
        DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEDUP_FILE.write_text(json.dumps(data))
    except Exception as e:
        _log(f"dedup record failed: {e}")


def _ocr(path: Path) -> str:
    """Apple Vision OCR (local, on-device via the OS framework)."""
    try:
        from ocrmac import ocrmac
        res = ocrmac.OCR(str(path)).recognize()
        return "\n".join(t for t, _conf, _box in res).strip()
    except Exception as e:  # never fail the capture over OCR
        _log(f"OCR failed for {path.name}: {e}")
        return ""


def _transcribe(path: Path) -> str:
    """Whisper (MLX) transcription; model auto-downloads on first use."""
    try:
        import mlx_whisper
        out = mlx_whisper.transcribe(str(path), path_or_hf_repo=WHISPER_MODEL)
        return (out.get("text") or "").strip()
    except Exception as e:
        _log(f"transcribe failed for {path.name}: {e}")
        return ""


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
    Failures land in logs/capture.log; the daily feeds-cron is the backstop."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = f'cd "{GBRAIN_DIR}" && gbrain import "{ING}" --no-embed && gbrain embed --stale'
    subprocess.Popen(["/bin/bash", "-lc", cmd], stdout=open(LOG, "a"),
                     stderr=subprocess.STDOUT, start_new_session=True)


# --- Voice inbox watcher (port of the old com.brain.voice 2-min launchd job) ---
# BlackHole meeting recordings / synced voice memos dropped into the inbox get
# transcribed into capture pages. Dedup is by CONTENT hash (old voice.py
# invariant: the same file dropped twice never re-transcribes).
VOICE_INBOX = Path(os.environ.get("VOICE_INBOX_DIR", str(HOME / "brain" / "inbox" / "audio")))
VOICE_SEEN = GBRAIN_DIR / "logs" / "voice-inbox-seen.json"
VOICE_MIN_BYTES = 32_000  # ≈ the old min_duration_s=3.0 floor
VOICE_SETTLE_S = 60  # skip files still being written/synced


def _voice_inbox_loop() -> None:
    while True:
        try:
            seen = json.loads(VOICE_SEEN.read_text()) if VOICE_SEEN.exists() else {}
        except Exception:
            seen = {}
        try:
            files = ([p for p in VOICE_INBOX.iterdir() if p.suffix.lower() in AUDIO_EXTS]
                     if VOICE_INBOX.exists() else [])
        except Exception:
            files = []
        changed = False
        for p in sorted(files):
            try:
                st = p.stat()
                if st.st_size < VOICE_MIN_BYTES or time.time() - st.st_mtime < VOICE_SETTLE_S:
                    continue
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                if h in seen:
                    continue
                text = _transcribe(p)
                # Mark seen even on empty transcript — an undecodable file must
                # not re-transcribe every cycle; the failure is in capture.log.
                seen[h] = ""
                if text:
                    page = _write_page(text, via="voice-inbox", file_rel=None)
                    seen[h] = page.name
                    _log(f"voice-inbox: {p.name} → {page.name}")
                else:
                    _log(f"voice-inbox: {p.name} produced no transcript")
                changed = True
            except Exception as e:  # noqa: BLE001 — one bad file never kills the loop
                _log(f"voice-inbox failed for {p.name}: {e}")
        if changed:
            try:
                VOICE_SEEN.parent.mkdir(parents=True, exist_ok=True)
                VOICE_SEEN.write_text(json.dumps(seen))
            except Exception as e:  # noqa: BLE001
                _log(f"voice-inbox state write failed: {e}")
            _ingest_async()
        time.sleep(120)


threading.Thread(target=_voice_inbox_loop, daemon=True).start()


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
    derived = ""
    key = None
    raw = None
    up = None

    if ctype.startswith("application/json"):
        data = await request.json()
        text = (data.get("text") or "").strip()
        via = (data.get("source") or via).strip() or via
        if text:
            key = hashlib.sha256(text.encode()).hexdigest()
    else:  # multipart: text field + optional file
        form = await request.form()
        text = (str(form.get("text") or "")).strip()
        via = (str(form.get("source") or via)).strip() or via
        up = form.get("file")
        if up is not None and hasattr(up, "filename") and up.filename:
            raw = await up.read()
            if len(raw) > MAX_BYTES:
                raise HTTPException(413, "attachment too large (max 40 MB)")
            key = hashlib.sha256(raw).hexdigest()  # file identity is the dedup key
        elif text:
            key = hashlib.sha256(text.encode()).hexdigest()

    # Content-hash dedup: same image bytes / same text within the window → no-op
    # (also avoids re-running OCR / Whisper on an accidental re-send).
    if key:
        dup = _dedup_seen(key)
        if dup:
            return JSONResponse({"ok": True, "duplicate": True, "page": dup, "via": via,
                                 "text": "(duplicate of a recent capture — skipped)"})

    # Not a duplicate → persist the file + derive text.
    if raw is not None and up is not None:
        FILE_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        ext = Path(up.filename).suffix.lower()[:6] or ".bin"
        dst = FILE_DIR / f"{ts}-{_slug(Path(up.filename).stem)}{ext}"
        dst.write_bytes(raw)
        file_rel = str(dst.relative_to(ING))
        if ext in IMAGE_EXTS:
            derived = _ocr(dst)
        elif ext in AUDIO_EXTS:
            derived = _transcribe(dst)

    full = "\n\n".join(p for p in (text, derived) if p).strip()
    if not full and not file_rel:
        raise HTTPException(400, "nothing to capture (empty text and no file)")

    page = _write_page(full, via, file_rel)
    if key:
        _dedup_record(key, page.name)
    _ingest_async()
    return JSONResponse({"ok": True, "page": page.name, "via": via,
                         "file": file_rel, "text": full})


# ---- Dashboard data adapter (parses gbrain CLI; reuses X-Brain-Key auth) ----

def _gbrain(args: list[str], timeout: int = 60) -> str:
    try:
        r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN_DIR),
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception as e:
        _log(f"gbrain {args[:2]} failed: {e}")
        return ""


def _parse_list(out: str) -> list[dict]:
    rows = []
    for line in out.splitlines():
        if "\t" not in line and "/" not in line:
            continue
        parts = line.split("\t")
        if "/" not in parts[0]:
            continue
        rows.append({
            "slug": parts[0].strip(),
            "type": parts[1].strip() if len(parts) > 1 else "",
            "date": parts[2].strip() if len(parts) > 2 else "",
            "title": parts[3].strip() if len(parts) > 3 else parts[0].strip(),
        })
    return rows


_SEARCH_RE = re.compile(r"^\[([\d.]+)\]\s+(\S+)\s+--\s+(.*)$")


def _parse_search(out: str) -> list[dict]:
    return [{"score": float(m.group(1)), "slug": m.group(2), "snippet": m.group(3).strip()}
            for m in (_SEARCH_RE.match(l) for l in out.splitlines()) if m]


def _svc(url: str) -> bool:
    try:
        import httpx
        return httpx.get(url, timeout=2).status_code == 200
    except Exception:
        return False


FEED_MAX_AGE_H = {"discord": 30, "x": 30, "youtube": 48, "telegram": 30, "digests": 30}

APPROVALS_ENV = HOME / ".gbrain" / "telegram_approvals.env"


def _proc_running(pattern: str) -> bool:
    """Liveness for port-less long-pollers (e.g. the telegram-approvals bot)."""
    try:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                              timeout=5).returncode == 0
    except Exception:
        return False


def _engine() -> str:
    try:
        return json.loads((HOME / ".gbrain" / "config.json").read_text()).get("engine", "pglite")
    except Exception:
        return "pglite"


def _postgres_ok() -> bool:
    """TCP liveness on the local Postgres. Only consulted when engine != pglite."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=2):
            return True
    except Exception:
        return False


def _newest_age_h(subdir: str) -> float | None:
    d = ING / subdir
    files = list(d.rglob("*.md")) if d.exists() else []
    if not files:
        return None
    return (time.time() - max(f.stat().st_mtime for f in files)) / 3600.0


def _field(content: str, key: str) -> str:
    """Read a frontmatter field. Handles inline values, quoted values, and YAML
    folded/literal block scalars (`>-`, `|`, …) by gathering indented lines."""
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", content, re.MULTILINE)
    if not m:
        return ""
    val = m.group(1).strip()
    if val and val[0] not in "|>":  # simple inline value
        return val.strip("'\"")
    # block scalar → gather the subsequent indented lines
    buf = []
    for ln in content[m.end():].splitlines():
        if not ln.strip():
            if buf:
                break
            continue
        if ln[:1] in (" ", "\t"):
            buf.append(ln.strip())
        else:
            break
    return " ".join(buf).strip().strip("'\"")


@app.get("/api/health")
def api_health(x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    services = {
        "ollama": _svc("http://127.0.0.1:11434/api/tags"),
        "bridge": _svc("http://127.0.0.1:8789/v1/models"),  # fast liveness; /health does a slow Claude ping
        "reranker": _svc("http://127.0.0.1:8081/health"),
        # OAuth MCP daemon (Hermes bridge). Crash-looped invisibly for 3 weeks
        # in Jul 2026 (post-reboot Postgres outage → launchd pended the spawn);
        # probing it here puts it on the SwiftBar verdict so that can't recur.
        "mcp": _svc("http://127.0.0.1:3131/health"),
    }
    # Conditional probes: a component only joins `services` (and thus the
    # verdict) once it is CONFIGURED — an intentionally-absent bot or a
    # not-yet-migrated Postgres must not paint the menubar red.
    if APPROVALS_ENV.exists():
        services["approvals"] = _proc_running("telegram-approvals/bot.py")
    engine = _engine()
    if engine != "pglite":
        services["postgres"] = _postgres_ok()
    feeds, stale = [], False
    for src, maxh in FEED_MAX_AGE_H.items():
        age = _newest_age_h(src)
        is_stale = age is not None and age > maxh
        stale = stale or is_stale
        feeds.append({"source": src, "age_hours": round(age, 1) if age is not None else None,
                      "stale": is_stale})
    down = not all(services.values())
    return {"verdict": "down" if down else ("warn" if stale else "ok"),
            "services": services, "feeds": feeds, "engine": engine}


@app.get("/api/pages")
def api_pages(source: str | None = None, type: str | None = None, tag: str | None = None,
              n: int = 30, sort: str | None = None, x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    args = ["list", "--limit", str(n)]
    if type:
        args += ["--type", type]
    if tag:
        args += ["--tag", tag]
    if sort in ("updated_desc", "created_desc", "slug"):
        args += ["--sort", sort]
    rows = _parse_list(_gbrain(args))
    if source:
        rows = [r for r in rows if r["slug"].startswith(f"{source}/")]
    return {"pages": rows}


@app.get("/api/page")
def api_page(slug: str, x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    return {"slug": slug, "content": _gbrain(["get", slug])}


@app.get("/api/search")
def api_search(q: str, n: int = 20, x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    return {"hits": _parse_search(_gbrain(["search", q, "--limit", str(n)]))}


@app.get("/api/today")
def api_today(x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    d = ING / "digests"
    files = sorted(d.glob("*.md")) if d.exists() else []
    if not files:
        return {"date": None, "content": ""}
    latest = files[-1]
    return {"date": latest.stem, "content": latest.read_text()}


@app.get("/api/needs")
def api_needs(x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    out = []
    for p in _parse_list(_gbrain(["list", "--type", "proposal", "--limit", "50"])):
        content = _gbrain(["get", p["slug"]])
        status = _field(content, "status") or "pending"
        if status.lower() != "pending":
            continue
        out.append({"slug": p["slug"], "title": p["title"], "status": status,
                    "action": _field(content, "action"), "target": _field(content, "target"),
                    "rationale": _field(content, "rationale"), "rollback": _field(content, "rollback")})
    return {"proposals": out}


@app.post("/api/decide")
async def api_decide(request: Request, x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    data = await request.json()
    slug, decision = data.get("slug"), data.get("decision")
    decided_by = re.sub(r"[^\w.-]", "", str(data.get("decided_by") or "dashboard"))[:32] or "dashboard"
    if not slug or decision not in ("approve", "reject"):
        raise HTTPException(400, "need slug + decision in {approve,reject}")
    content = _gbrain(["get", slug])
    if not content.strip():
        raise HTTPException(404, "proposal not found")
    new_status = "approved" if decision == "approve" else "rejected"
    if re.search(r"^status:\s*.+$", content, re.MULTILINE):
        content = re.sub(r"^status:\s*.+$", f"status: {new_status}", content, count=1, flags=re.MULTILINE)
    else:
        content = re.sub(r"^(---\n)", rf"\1status: {new_status}\n", content, count=1)
    # decision audit (cheap provenance — who/when the status flipped)
    decided_at = datetime.now().astimezone().isoformat(timespec="seconds")
    content = re.sub(r"^decided_(at|by):\s*.+\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^(---\n)", rf"\1decided_by: {decided_by}\ndecided_at: {decided_at}\n", content, count=1)
    _gbrain(["put", slug, "--content", content])
    # Write-through to the ingest file: `gbrain import` skips a file only when
    # its hash matches the LIVE page's content_hash, so a brain-only status
    # flip would be silently REVERTED to pending by the next nightly import of
    # ~/brains-ingest. Keeping file == page makes the decision durable.
    try:
        ing_file = (ING / f"{slug}.md").resolve()
        if ing_file.is_relative_to(ING.resolve()) and ing_file.exists():
            ing_file.write_text(content)
    except Exception as e:  # noqa: BLE001 — the brain page is still decided; log and move on
        _log(f"decide write-through failed for {slug}: {e}")
    return {"ok": True, "slug": slug, "status": new_status}


@app.post("/api/propose")
async def api_propose(request: Request, x_brain_key: str | None = Header(default=None)):
    """Write a proposal page (skills/conventions/proposals.md) on behalf of an
    external agent (Hermes shim, Forge). Five-field discipline enforced here so
    every /api/needs item is decidable at a glance. The page lands in
    brains-ingest/proposals/ and is imported async, same as a capture."""
    _require(x_brain_key)
    data = await request.json()
    required = ("action", "target", "rationale", "rollback", "proposed_by")
    missing = [k for k in required if not str(data.get(k) or "").strip()]
    if missing:
        raise HTTPException(400, f"proposal missing required fields: {', '.join(missing)}")
    title = re.sub(r"\s+", " ", str(data.get("title") or data["action"])).strip()[:120]
    body = str(data.get("body") or "").strip()
    now = datetime.now().astimezone()
    prop_dir = ING / "proposals"
    prop_dir.mkdir(parents=True, exist_ok=True)
    name = f"{now.strftime('%Y-%m-%d')}-{_slug(title)}"
    if (prop_dir / f"{name}.md").exists():  # same title same day → disambiguate
        name = f"{name}-{now.strftime('%H%M%S')}"
    ystr = lambda s: json.dumps(str(s))  # JSON string is valid single-line YAML
    lines = [
        "---",
        f"title: {ystr(title)}",
        "type: proposal",
        "status: pending",
        f"action: {data['action']}",
        f"target: {ystr(data['target'])}",
        f"rationale: {ystr(data['rationale'])}",
        f"rollback: {ystr(data['rollback'])}",
        f"proposed_by: {data['proposed_by']}",
        f"created: {now.strftime('%Y-%m-%d')}",
        "tags: [proposal]",
        "---",
        "",
        f"# {title}",
        "",
        body or "_(no draft content)_",
    ]
    path = prop_dir / f"{name}.md"
    path.write_text("\n".join(lines) + "\n")
    _log(f"propose: {data['proposed_by']} → {path.name}")
    _ingest_async()
    return JSONResponse({"ok": True, "slug": f"proposals/{name}", "status": "pending"})


# ---- Console adapter: stats / ops / graph / ask (all gbrain-backed) ----

def _num(text: str, label: str) -> int:
    m = re.search(rf"^{re.escape(label)}:\s*([\d,]+)", text, re.MULTILINE)
    return int(m.group(1).replace(",", "")) if m else 0


@app.get("/api/stats")
def api_stats(x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    out = _gbrain(["stats"])
    by_type: dict[str, int] = {}
    seen_header = False
    for line in out.splitlines():
        if line.strip().lower().startswith("by type"):
            seen_header = True
            continue
        if seen_header:
            mm = re.match(r"\s+([\w-]+):\s*(\d+)", line)
            if mm:
                by_type[mm.group(1)] = int(mm.group(2))
    return {"pages": _num(out, "Pages"), "chunks": _num(out, "Chunks"),
            "embedded": _num(out, "Embedded"), "links": _num(out, "Links"),
            "tags": _num(out, "Tags"), "timeline": _num(out, "Timeline"),
            "by_type": by_type}


@app.get("/api/ops")
def api_ops(x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    health = api_health(x_brain_key)
    stats = api_stats(x_brain_key)
    ht = _gbrain(["health"])

    def hf(label: str):
        m = re.search(rf"{re.escape(label)}:\s*([\d.]+)", ht)
        return float(m.group(1)) if m else None

    score = re.search(r"Health score:\s*(\d+)\s*/\s*(\d+)", ht)
    detail = {
        "score": int(score.group(1)) if score else None,
        "score_max": int(score.group(2)) if score else None,
        "embed_coverage": hf("Embed coverage"),
        "missing_embeddings": int(hf("Missing embeddings") or 0),
        "stale_pages": int(hf("Stale pages") or 0),
        "orphan_pages": int(hf("Orphan pages") or 0),
    }
    jobs_raw = _gbrain(["jobs", "list"])
    jobs: list[dict] = []
    if "No jobs" not in jobs_raw:
        for line in jobs_raw.splitlines():
            parts = [p for p in line.split() if p]
            if len(parts) >= 2:
                jobs.append({"id": parts[0], "status": parts[1], "raw": line.strip()})
    return {"health": health, "stats": stats, "detail": detail, "jobs": jobs}


def _ego_edges(slug: str, outbound: bool = True) -> list[dict]:
    """Edges incident to `slug`: inbound via `backlinks` (clean JSON), outbound
    via `graph` traversal. `graph-query` returns nothing for inbound-only nodes
    (e.g. ticker pages), so backlinks is the reliable source."""
    edges: list[dict] = []
    try:
        for e in json.loads(_gbrain(["backlinks", slug]) or "[]"):
            fr, to = e.get("from_slug"), e.get("to_slug")
            if fr and to:
                edges.append({"from": fr, "to": to, "type": e.get("link_type", "")})
    except Exception:
        pass
    if outbound:
        try:
            for n in json.loads(_gbrain(["graph", slug, "--depth", "1"]) or "[]"):
                for lk in (n.get("links") or []):
                    to = lk.get("to_slug") or lk.get("slug")
                    if to and n.get("slug"):
                        edges.append({"from": n["slug"], "to": to, "type": lk.get("link_type", "")})
        except Exception:
            pass
    return edges


def _top_hubs(k: int = 6) -> list[str]:
    """Densest ticker hubs by inbound Tier-0 degree, read from the extractor's
    local state file (fast, no subprocess, no 100-row `list` cap). The mention
    edges it records ARE the density that makes a legible seed graph."""
    try:
        st = json.loads((GBRAIN_DIR / "logs" / "entity-extract-state.json").read_text())
    except Exception:
        return []
    deg: dict[str, int] = {}
    for edge in st.get("edges", []):
        if len(edge) >= 2 and str(edge[1]).startswith("ticker/"):
            deg[edge[1]] = deg.get(edge[1], 0) + 1
    return [s for s, _ in sorted(deg.items(), key=lambda kv: -kv[1])[:k]]


def _relation_graph(seeds: list[str], limit: int) -> dict | None:
    """Union of the ego-graphs of several hub slugs → a real multi-hub network.
    Shared source pages (a bookmark citing $NVDA and $MU) connect hubs, so it
    reads as a graph, not a set of stars. Returns None if no edges."""
    hubset = set(seeds)
    edges: list[dict] = []
    seen: set[tuple] = set()
    for s in seeds:
        for e in _ego_edges(s, outbound=True):
            key = (e["from"], e["to"], e["type"])
            if key not in seen:
                seen.add(key)
                edges.append(e)
        if len(edges) >= limit:
            break
    edges = edges[:limit]
    if not edges:
        return None
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["from"]] = deg.get(e["from"], 0) + 1
        deg[e["to"]] = deg.get(e["to"], 0) + 1
    slugs = {e["from"] for e in edges} | {e["to"] for e in edges} | hubset
    nodes = [{"slug": s, "source": s.split("/")[0], "type": "",
              "title": s.split("/")[-1], "hub": s in hubset, "deg": deg.get(s, 0)}
             for s in slugs]
    src_counts: dict[str, int] = {}
    for n in nodes:
        src_counts[n["source"]] = src_counts.get(n["source"], 0) + 1
    sources = sorted(({"source": s, "count": c} for s, c in src_counts.items()), key=lambda x: -x["count"])
    return {"mode": "relations", "seed": seeds[0], "seeds": seeds, "auto_seed": seeds[0],
            "nodes": nodes, "edges": edges, "sources": sources}


@app.get("/api/graph")
def api_graph(slug: str | None = None, depth: int = 2, limit: int = 400,
              x_brain_key: str | None = Header(default=None)):
    """Relation traversal when links exist; otherwise a corpus source-map
    (pages clustered by source, sized by volume) — honest to the data on hand.

    With no slug, auto-seeds from the densest ticker hubs so the Graph opens on a
    real relation network instead of an empty source-map."""
    _require(x_brain_key)
    if slug:
        g = _relation_graph([slug], limit)
        if g:
            g["seeds"] = [slug]
            g.pop("auto_seed", None)
            return g
    else:
        hubs = _top_hubs(6)
        if hubs:
            g = _relation_graph(hubs, limit)
            if g:
                return g
    rows = _parse_list(_gbrain(["list", "--limit", str(limit)]))
    counts: dict[str, int] = {}
    nodes = []
    for r in rows:
        src = r["slug"].split("/")[0]
        counts[src] = counts.get(src, 0) + 1
        nodes.append({"slug": r["slug"], "source": src, "type": r["type"], "title": r["title"]})
    sources = sorted(({"source": s, "count": c} for s, c in counts.items()),
                     key=lambda x: -x["count"])
    return {"mode": "corpus", "nodes": nodes, "sources": sources, "edges": []}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


_SLUG_RE = re.compile(r"\b([a-z0-9][\w.-]*/[\w./-]+)\b")


def _grounding(text: str, cap: int = 8) -> list[str]:
    out, seen = [], set()
    for m in _SLUG_RE.finditer(text):
        s = m.group(1).rstrip(".,)")
        if s not in seen and "/" in s:
            seen.add(s)
            out.append(s)
        if len(out) >= cap:
            break
    return out


@app.post("/api/ask")
async def api_ask(request: Request, x_brain_key: str | None = Header(default=None)):
    """The persistent AI-agent surface. Streams `gbrain think` (cited multi-hop
    synthesis) over SSE; falls back to `gbrain query` (fast hybrid, no LLM) when
    ?fast or the Claude bridge is down. Emits start → token* → done{grounding}."""
    _require(x_brain_key)
    data = await request.json()
    q = (data.get("q") or data.get("question") or "").strip()
    if not q:
        raise HTTPException(400, "need q")
    fast = bool(data.get("fast"))
    bridge_up = _svc("http://127.0.0.1:8789/v1/models")
    use_think = (not fast) and bridge_up
    mode = "think" if use_think else "query"

    def gen():
        yield _sse({"event": "start", "mode": mode})
        args = ["think", q] if use_think else ["query", q, "--limit", "8"]
        buf: list[str] = []
        try:
            proc = subprocess.Popen(["gbrain", *args], cwd=str(GBRAIN_DIR),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            assert proc.stdout is not None
            for line in proc.stdout:
                buf.append(line)
                yield _sse({"event": "token", "text": line})
            proc.wait(timeout=240)
            yield _sse({"event": "done", "grounding": _grounding("".join(buf))})
        except Exception as e:
            _log(f"ask failed: {e}")
            yield _sse({"event": "error", "detail": str(e)[:200]})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@app.get("/api/findings")
def api_findings(x_brain_key: str | None = Header(default=None)):
    """Ranked convergence findings (the '1+1=3' output), newest/highest first."""
    _require(x_brain_key)
    out = []
    for p in _parse_list(_gbrain(["list", "--type", "finding", "--limit", "50"])):
        content = _gbrain(["get", p["slug"]])
        if _field(content, "status").lower() == "resolved":
            continue
        try:
            score = float(_field(content, "score") or 0)
        except ValueError:
            score = 0.0
        out.append({"slug": p["slug"], "ticker": _field(content, "ticker"),
                    "score": score, "window": _field(content, "window"),
                    "classes": [c.lower() for c in _parse_watchlist(content, "classes")],
                    "title": p["title"]})
    out.sort(key=lambda x: (-x["score"], -len(x["classes"])))  # score, then class-diversity
    return {"findings": out}


def _parse_watchlist(content: str, key: str) -> list[str]:
    """Parse a frontmatter list — inline `key: [a, b]` OR block `key:\\n  - a`."""
    lines = content.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(rf"^{re.escape(key)}:\s*(.*)$", ln)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            inner = rest[1:rest.rfind("]")] if "]" in rest else rest[1:]
            return [s.strip().strip("'\"").upper() for s in inner.split(",") if s.strip()]
        out = []
        for ln2 in lines[i + 1:]:
            mm = re.match(r"^\s*-\s*(.+)$", ln2)
            if mm:
                out.append(mm.group(1).strip().strip("'\"").upper())
            elif ln2.strip() == "":
                continue
            else:
                break
        return out
    return []


@app.get("/api/watchlist")
def api_watchlist(x_brain_key: str | None = Header(default=None)):
    """The Forge-owned watchlist (held/watched tickers) that drives the pollers."""
    _require(x_brain_key)
    content = _gbrain(["get", "finance/watchlist"])
    return {"held": _parse_watchlist(content, "held"), "watched": _parse_watchlist(content, "watched")}


@app.put("/api/watchlist")
async def api_watchlist_put(request: Request, x_brain_key: str | None = Header(default=None)):
    """Forge (or the dashboard) writes the watchlist. Overwrites finance/watchlist."""
    _require(x_brain_key)
    data = await request.json()
    held = [str(s).upper().strip() for s in (data.get("held") or []) if str(s).strip()]
    watched = [str(s).upper().strip() for s in (data.get("watched") or []) if str(s).strip()]
    by = str(data.get("written_by") or "api")
    body = ("---\ntype: note\ntitle: Watchlist\nsource: watchlist\n"
            f"written_by: {by}\nheld: [{', '.join(held)}]\nwatched: [{', '.join(watched)}]\n"
            "---\n\n# Watchlist\n\nForge-owned source of truth for held/watched tickers. "
            "The EDGAR / permit / convergence pollers read this page.\n")
    _gbrain(["put", "finance/watchlist", "--content", body])
    return {"ok": True, "held": held, "watched": watched}


def _parse_tv_text(raw: str) -> dict:
    """TradingView alerts send JSON when you template it, else a freeform string.
    Accept `k=v;k=v`, or scrape `SYMBOL ... 123.4` from prose."""
    out: dict = {}
    if "=" in raw and "{" not in raw:
        for part in re.split(r"[;\n]", raw):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip().lower()] = v.strip()
    if "symbol" not in out and "ticker" not in out:
        m = re.search(r"\b([A-Z]{1,6})(?::[A-Z]{1,6})?\b", raw)
        if m:
            out["symbol"] = m.group(1)
    if "price" not in out and "level" not in out:
        m = re.search(r"(\d+(?:\.\d+)?)", raw)
        if m:
            out["price"] = m.group(1)
    if "message" not in out:
        out["message"] = raw.strip()[:280]
    return out


def _ensure_ticker(sym: str) -> str:
    slug = f"ticker/{sym.lower()}"
    if not (_gbrain(["get", slug]) or "").strip():
        _gbrain(["put", slug, "--content",
                 f"---\ntype: ticker\ntitle: {sym}\nsymbol: {sym}\nsource: tradingview\ntags: [ticker]\n---\n\n"
                 f"# {sym}\n\nTicker hub.\n"])
    return slug


@app.post("/webhooks/tradingview")
async def tradingview_webhook(request: Request):
    """TradingView alert → an `index_level` signal page + a `tracks_level` edge to
    the ticker, so a level break becomes the price/level signal class the
    convergence engine reads. TV can't send custom headers reliably, so the shared
    secret rides in the body/query (`token=`), checked against Keychain
    `TRADINGVIEW_TOKEN` when configured (public over the Funnel — set one)."""
    raw = (await request.body()).decode("utf-8", "ignore")
    try:
        payload = json.loads(raw) if raw.strip().startswith("{") else _parse_tv_text(raw)
    except Exception:
        payload = _parse_tv_text(raw)
    # _secret propagates a Keychain failure → 500, which is the safe direction
    # here (fail closed): this endpoint is public over the Funnel.
    want = _secret("TRADINGVIEW_TOKEN")
    if want and (payload.get("token") or request.query_params.get("token")) != want:
        raise HTTPException(401, "bad tradingview token")
    sym = re.sub(r"^[A-Za-z]+:", "", str(payload.get("symbol") or payload.get("ticker") or "")).upper().strip()
    if not re.fullmatch(r"[A-Z]{1,6}", sym):
        raise HTTPException(400, "no valid symbol in alert")
    price = str(payload.get("price") or payload.get("close") or "").strip()
    level = str(payload.get("level") or payload.get("value") or price).strip()
    direction = str(payload.get("direction") or payload.get("action") or "").lower().strip()
    msg = str(payload.get("message") or payload.get("comment") or raw[:200]).replace('"', "'").strip()
    day = datetime.now().strftime("%Y-%m-%d")
    slug = f"index_level/{sym.lower()}-{day}"
    body = (f"---\ntype: index_level\ntitle: \"{sym} level {direction or 'alert'} {level}\"\n"
            f"source: tradingview\nsymbol: {sym}\nlevel: \"{level}\"\nprice: \"{price}\"\n"
            f"direction: {direction or 'na'}\ndate: {day}\ntags: [level, tradingview, {sym.lower()}]\n---\n\n"
            f"# {sym} — level {direction or 'alert'} {level}\n\n"
            f"- **Symbol:** ${sym}  ·  **Level:** {level}  ·  **Price:** {price}  ·  **Direction:** {direction or 'n/a'}\n"
            f"- **Alert:** {msg}\n")
    tslug = _ensure_ticker(sym)
    _gbrain(["put", slug, "--content", body])
    _gbrain(["link", slug, tslug, "--link-type", "tracks_level", "--link-source", "tradingview"])
    _log(f"tradingview alert: {sym} {direction} {level} → {slug}")
    return {"ok": True, "symbol": sym, "level": level, "slug": slug}


@app.post("/internal/post-digest")
async def internal_post_digest(request: Request):
    """Deliver the latest daily digest to Discord FROM the always-on sidecar, which
    holds Keychain access. The short-lived feeds-cron can't read the Keychain when
    the Mac slept through 06:30 (login keychain locked → -25320), so delivery moved
    here. Localhost-only (no key needed — the cron curls 127.0.0.1)."""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "internal endpoint (localhost only)")
    try:
        webhook = _secret("DISCORD_WEBHOOK_URL")
    except Exception as e:  # noqa: BLE001 — locked keychain + no file yet
        _log(f"digest webhook secret unavailable: {e}")
        return {"ok": False, "reason": "webhook secret unavailable (keychain locked?)"}
    if not webhook:
        return {"ok": False, "reason": "no DISCORD_WEBHOOK_URL"}
    ddir = HOME / "brains-ingest" / "digests"
    files = sorted(ddir.glob("*.md"), reverse=True) if ddir.exists() else []
    if not files:
        return {"ok": False, "reason": "no digest page"}
    raw = files[0].read_text()
    body = re.sub(r"^---.*?---\n", "", raw, count=1, flags=re.DOTALL).strip()
    text = f"**🧠 BRAINS daily digest — {files[0].stem}**\n\n{body}"
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > 1900 and cur:
            chunks.append(cur); cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    sent = 0
    for ch in chunks[:6]:
        try:
            # Discord requires a real User-Agent — the default Python-urllib UA 403s.
            req = urllib.request.Request(webhook, data=json.dumps({"content": ch}).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "BRAINS-digest/1.0 (+https://github.com/awvmeijer/gbrain)"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=15)
            sent += 1
        except Exception as e:
            _log(f"digest post chunk failed: {e}")
    return {"ok": sent > 0, "sent": sent, "digest": files[0].stem}


def _ics_esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@app.get("/calendar/todos.ics")
def todos_ics(key: str | None = None):
    """Subscribable calendar of todos/proposals with a `due` date. Calendar apps
    subscribe with `?key=<CAPTURE_KEY>` in the URL (they can't send headers). Each
    due-bearing todo/proposal → an all-day VEVENT; the label carries the status."""
    _require(key)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BRAINS//todos//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:BRAINS todos"]
    rows = _parse_list(_gbrain(["list", "--type", "todo", "--limit", "100"])) + \
        _parse_list(_gbrain(["list", "--type", "proposal", "--limit", "100"]))
    for p in rows:
        content = _gbrain(["get", p["slug"]])
        due = re.sub(r"[^0-9]", "", _field(content, "due") or "")[:8]
        if len(due) != 8:
            continue
        status = (_field(content, "status") or "open").lower()
        summ = p["title"] or "todo"
        if status not in ("open", "pending"):
            summ = f"[{status}] {summ}"
        desc = (_field(content, "rationale") or _field(content, "note") or "").replace("\n", " ")[:200]
        uid = re.sub(r"[^a-z0-9]+", "-", p["slug"].lower())
        lines += ["BEGIN:VEVENT", f"UID:{uid}@brains", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{due}", f"SUMMARY:{_ics_esc(summ)}",
                  f"DESCRIPTION:{_ics_esc(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar")


def _repo_slug(repo: str) -> str:
    """Canonical repo (git remote or path) → lowercase gbrain slug."""
    s = re.sub(r"^\w+://", "", (repo or "").strip()).replace(".git", "")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "local"


@app.get("/api/context")
def api_context(repo: str = "", x_brain_key: str | None = Header(default=None)):
    """Per-repo brain context for the Claude Code SessionStart hook (≤4KB markdown)."""
    _require(x_brain_key)
    slug = _repo_slug(repo)
    lines: list[str] = []
    sessions = [r for r in _parse_list(_gbrain(["list", "--type", "session", "--limit", "50"]))
                if r["slug"].startswith(f"sessions/{slug}/")][:3]
    if sessions:
        lines.append("**Recent Claude Code sessions in this repo:**")
        for r in sessions:
            lines.append(f"- {r['date']} — {r['title']}")
    q = (repo.rstrip("/").split("/")[-1] or slug).replace("-", " ")
    hits = _parse_search(_gbrain(["query", q, "--limit", "3"]))
    if hits:
        lines.append("\n**Related in the brain:**")
        for h in hits:
            lines.append(f"- `{h['slug']}` — {h['snippet'][:70]}")
    md = ("### BRAINS/ — what the brain knows about this repo\n" + "\n".join(lines))[:4000] if lines else ""
    return {"repo": repo, "slug": slug, "context": md}


@app.post("/api/session-record")
async def api_session_record(request: Request, x_brain_key: str | None = Header(default=None)):
    """SessionEnd → one immutable session page (repo, files touched, commits)."""
    _require(x_brain_key)
    d = await request.json()
    repo = str(d.get("repo") or "")
    slug = _repo_slug(repo)
    ts = re.sub(r"[^0-9-]", "", str(d.get("ts") or "").replace("T", "-")).strip("-")[:19] \
        or datetime.now().strftime("%Y-%m-%d-%H%M%S")
    title = (str(d.get("title") or "session").strip() or "session")[:80].replace('"', "'")
    files = [str(f) for f in (d.get("files") or [])][:40]
    commits = [str(c) for c in (d.get("commits") or [])][:20]
    sid = str(d.get("session_id") or "")
    body = [
        "---", "type: session", f'title: "{title}"', "source: claude-code",
        f"repo: {repo}", f"session_id: {sid}", f"date: {datetime.now().strftime('%Y-%m-%d')}",
        "tags: [session, claude-code]", "---", "", f"# Session — {title}", "", f"**Repo:** {repo}", "",
    ]
    if files:
        body += ["## Files touched"] + [f"- `{f}`" for f in files] + [""]
    if commits:
        body += ["## Commits this session"] + [f"- {c}" for c in commits] + [""]
    fslug = f"sessions/{slug}/{ts}"
    _gbrain(["put", fslug, "--content", "\n".join(body)])
    return {"ok": True, "slug": fslug}


# Self-destructing service worker: the retired PWA registered /sw.js (cache-first
# shell) at this origin, so browsers still serve its cached old UI. The browser
# re-fetches /sw.js from network on update checks; serving this unregisters the
# old SW, purges all caches, and reloads open tabs → the real dashboard loads.
_SW_KILL = """self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => { e.waitUntil((async () => {
  try { await self.registration.unregister(); } catch (x) {}
  try { const ks = await caches.keys(); await Promise.all(ks.map(k => caches.delete(k))); } catch (x) {}
  const cs = await self.clients.matchAll({ type: 'window' });
  cs.forEach((c) => c.navigate(c.url));
})()); });
"""


@app.get("/sw.js")
def sw_kill():
    return Response(_SW_KILL, media_type="application/javascript",
                    headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/"})


_PAGE = """<!doctype html><html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=apple-mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-title content="Brain">
<title>Brain Capture</title>
<style>
:root{--bg:#0e0e12;--panel:#17171d;--line:#2a2a33;--ink:#ececf2;--dim:#9a9aa8;--violet:#a8a4ff;--live:#39FF14}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 -apple-system,system-ui,sans-serif;padding:max(16px,env(safe-area-inset-top)) 16px 32px}
h1{font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin:4px 0 16px}
h1 b{color:var(--ink)} h1 span{color:var(--violet)}
textarea{width:100%;min-height:120px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:12px;font:inherit;resize:vertical}
.row{display:flex;gap:10px;margin-top:12px}
button{flex:1;padding:15px;border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--ink);font:600 15px/1 inherit}
button:active{transform:scale(.98)}
.send{background:var(--violet);color:#111;border-color:var(--violet)}
.rec.on{background:var(--live);color:#111;border-color:var(--live)}
.photo{position:relative;overflow:hidden}
.photo input{position:absolute;inset:0;opacity:0;font-size:200px}
#status{margin-top:16px;padding:12px;border:1px dashed var(--line);border-radius:12px;color:var(--dim);min-height:20px;white-space:pre-wrap;font-size:14px}
#status.ok{color:var(--ink);border-color:var(--violet)} #status.err{color:#ff8a8a;border-color:#ff8a8a}
.key{display:flex;gap:8px;margin-bottom:14px} .key input{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:10px}
.gear{color:var(--dim);text-decoration:none;font-size:13px;float:right}
</style></head><body>
<a class=gear href="#" onclick="setKey();return false">key</a>
<h1><b>BRAINS</b><span>/</span> capture</h1>
<div class=key id=keyrow style=display:none>
  <input id=key type=password placeholder="paste CAPTURE_KEY" autocomplete=off>
  <button style=flex:0;padding:10px 16px onclick=saveKey()>save</button>
</div>
<textarea id=t placeholder="Type or paste anything — a thought, a ticker, a link…"></textarea>
<div class=row>
  <button class=send onclick=sendText()>Send</button>
  <button class=rec id=rec onclick=toggleRec()>🎤 Record</button>
</div>
<div class=row>
  <button class=photo>📷 Photo / Screenshot<input type=file accept="image/*" onchange=sendFile(this.files[0],'image')></button>
</div>
<div id=status>Ready.</div>
<script>
const S=document.getElementById('status'),T=document.getElementById('t');
function K(){return localStorage.getItem('capture_key')||''}
function setKey(){document.getElementById('keyrow').style.display='flex';document.getElementById('key').value=K()}
function saveKey(){localStorage.setItem('capture_key',document.getElementById('key').value.trim());document.getElementById('keyrow').style.display='none';msg('Key saved.','ok')}
function msg(m,c){S.textContent=m;S.className=c||''}
if(!K())setKey();
async function post(body,isForm){
  if(!K()){msg('Set your key first (tap "key").','err');return}
  msg('Sending…');
  try{
    const h={'X-Brain-Key':K()}; if(!isForm)h['Content-Type']='application/json';
    const r=await fetch('/capture',{method:'POST',headers:h,body:isForm?body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok){msg('Error: '+(j.detail||r.status),'err');return}
    const tag=j.duplicate?'Duplicate — skipped':'Captured ✓ ('+j.via+')';
    msg(tag+'\\n'+(j.text||'').slice(0,280),'ok');
    if(!isForm&&!j.duplicate)T.value='';
  }catch(e){msg('Network error: '+e,'err')}
}
function sendText(){const v=T.value.trim();if(!v){msg('Nothing to send.','err');return}post({text:v,source:'web'})}
function sendFile(f,src){if(!f)return;const fd=new FormData();fd.append('source',src);fd.append('file',f,f.name||src);post(fd,true)}
let mr,chunks;
async function toggleRec(){
  const b=document.getElementById('rec');
  if(mr&&mr.state==='recording'){mr.stop();return}
  try{
    const s=await navigator.mediaDevices.getUserMedia({audio:true});
    mr=new MediaRecorder(s);chunks=[];
    mr.ondataavailable=e=>chunks.push(e.data);
    mr.onstop=()=>{b.classList.remove('on');b.textContent='🎤 Record';
      const blob=new Blob(chunks,{type:mr.mimeType||'audio/mp4'});
      const ext=(blob.type.includes('webm'))?'webm':'m4a';
      msg('Transcribing…');sendFile(new File([blob],'voice.'+ext,{type:blob.type}),'voice');
      s.getTracks().forEach(t=>t.stop())};
    mr.start();b.classList.add('on');b.textContent='● Stop';msg('Recording… tap Stop.');
  }catch(e){msg('Mic blocked: '+e,'err')}
}
</script></body></html>"""


# The built v3 dashboard is served at / (mounted LAST so /api, /capture, /health
# win). Falls back to the inline capture page when the dashboard isn't built.
if DASHBOARD_DIST.exists():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIST), html=True), name="dashboard")
else:
    @app.get("/", response_class=HTMLResponse)
    def ui():
        return _PAGE
