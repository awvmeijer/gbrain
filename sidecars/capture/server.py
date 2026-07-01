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
import time
from datetime import datetime
from pathlib import Path

# Whisper (and its ffmpeg decode step) + bun/gbrain need Homebrew on PATH.
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "") + f":{Path.home()}/.bun/bin"

import keyring
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
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


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")


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


def _newest_age_h(subdir: str) -> float | None:
    d = ING / subdir
    files = list(d.rglob("*.md")) if d.exists() else []
    if not files:
        return None
    return (time.time() - max(f.stat().st_mtime for f in files)) / 3600.0


def _field(content: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""


@app.get("/api/health")
def api_health(x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    services = {
        "ollama": _svc("http://127.0.0.1:11434/api/tags"),
        "bridge": _svc("http://127.0.0.1:8789/v1/models"),  # fast liveness; /health does a slow Claude ping
        "reranker": _svc("http://127.0.0.1:8081/health"),
    }
    feeds, stale = [], False
    for src, maxh in FEED_MAX_AGE_H.items():
        age = _newest_age_h(src)
        is_stale = age is not None and age > maxh
        stale = stale or is_stale
        feeds.append({"source": src, "age_hours": round(age, 1) if age is not None else None,
                      "stale": is_stale})
    down = not all(services.values())
    return {"verdict": "down" if down else ("warn" if stale else "ok"),
            "services": services, "feeds": feeds}


@app.get("/api/pages")
def api_pages(source: str | None = None, type: str | None = None, tag: str | None = None,
              n: int = 30, x_brain_key: str | None = Header(default=None)):
    _require(x_brain_key)
    args = ["list", "--limit", str(n)]
    if type:
        args += ["--type", type]
    if tag:
        args += ["--tag", tag]
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
    _gbrain(["put", slug, "--content", content])
    return {"ok": True, "slug": slug, "status": new_status}


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
