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

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

# Whisper (and its ffmpeg decode step) + bun/gbrain need Homebrew on PATH.
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "") + f":{Path.home()}/.bun/bin"

import keyring
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

SERVICE = "brain"
HOME = Path.home()
GBRAIN_DIR = HOME / "gbrain"
ING = HOME / "brains-ingest"
CAP_DIR = ING / "capture"
FILE_DIR = CAP_DIR / "files"
LOG = GBRAIN_DIR / "logs" / "capture.log"
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


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")


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

    if ctype.startswith("application/json"):
        data = await request.json()
        text = (data.get("text") or "").strip()
        via = (data.get("source") or via).strip() or via
    else:  # multipart: text field + optional file
        form = await request.form()
        text = (str(form.get("text") or "")).strip()
        via = (str(form.get("source") or via)).strip() or via
        up = form.get("file")
        if up is not None and hasattr(up, "filename") and up.filename:
            raw = await up.read()
            if len(raw) > MAX_BYTES:
                raise HTTPException(413, "attachment too large (max 40 MB)")
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
    _ingest_async()
    return JSONResponse({"ok": True, "page": page.name, "via": via,
                         "file": file_rel, "text": full})


@app.get("/", response_class=HTMLResponse)
def ui():
    return _PAGE


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
    msg('Captured ✓ ('+j.via+')\\n'+(j.text||'').slice(0,280),'ok');
    if(!isForm)T.value='';
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
