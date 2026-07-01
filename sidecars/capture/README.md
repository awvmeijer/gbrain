# brains-capture — phone → brain over Tailscale (web app, no iCloud, no Shortcuts)

A tiny auth'd HTTP receiver + a **mobile web app** so the phone can push **text,
voice, or photos** into the brain over the existing Tailscale Funnel — no iCloud,
no iOS Shortcuts. OCR and transcription run **locally on the Mac** (Apple Vision +
Whisper/MLX), so voice memos and screenshots become searchable text.

## Use it (the web app)

1. On the phone, open **`https://anthonys-macbook-pro.tailc008aa.ts.net/`**
   (works on any network — Funnel is public HTTPS; no Tailscale app needed).
2. Tap **key**, paste your `CAPTURE_KEY` (stored in the browser, once).
3. **Share → Add to Home Screen** → it launches full-screen like a native app.

Then:
- **Type / paste** anything → **Send**.
- **🎤 Record** → tap to record a voice note, tap Stop → transcribed on the Mac.
- **📷 Photo / Screenshot** → snap or pick an image → OCR'd on the Mac.

Each capture confirms inline with the extracted text.

## How it runs

- Service: `uvicorn server:app` on `127.0.0.1:8787`, kept alive by
  `infra/launchd/com.brains.capture.plist`.
- The Funnel forwards `https://anthonys-macbook-pro.tailc008aa.ts.net/` →
  `127.0.0.1:8787` (the old `~/brain` API that owned :8787 is retired).
- Auth: every POST needs `X-Brain-Key: <CAPTURE_KEY>` (Keychain service `brain`).
  Public endpoint → key mandatory; it only *writes*, so a leaked key = spam risk.
- Local processing: images → **Apple Vision OCR** (`ocrmac`); audio → **Whisper**
  (`mlx-whisper`, model `WHISPER_MODEL`, default `mlx-community/whisper-small-mlx`,
  auto-downloads on first use). Needs `ffmpeg` on PATH (set in the plist).
- Each capture → `~/brains-ingest/capture/<ts>-<slug>.md` (attachments under
  `capture/files/`) + a background `gbrain import + embed --stale` → queryable in
  seconds. The daily feeds-cron is the backstop.

```
GET  /            → the mobile capture web app
GET  /health      → {"ok": true, "key_configured": true}
POST /capture     JSON {text, source?}  OR  multipart {text?, source?, file}
                  images are OCR'd, audio is transcribed, into the page text
```

Read the key for first-time setup:
```bash
/Users/awvmeijer/gbrain/sidecars/.venv/bin/python -m keyring get brain CAPTURE_KEY
```

## Test from the Mac
```bash
KEY=$(/Users/awvmeijer/gbrain/sidecars/.venv/bin/python -m keyring get brain CAPTURE_KEY)
curl -X POST https://anthonys-macbook-pro.tailc008aa.ts.net/capture \
  -H "X-Brain-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"text":"hello from the phone","source":"web"}'
```

> This replaces the iCloud `manual-capture` recipe (iCloud Drive is full). An iOS
> Shortcut can still POST to `/capture` if you ever want Share-Sheet/Back-Tap
> entry points, but the web app needs no Shortcuts at all.
