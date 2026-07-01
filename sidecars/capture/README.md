# brains-capture — phone → brain over Tailscale (no iCloud)

A tiny auth'd HTTP receiver so an iOS Shortcut can push **text, voice, or image**
captures straight into the brain over the existing Tailscale Funnel — no iCloud,
no file-sync. OCR and speech-to-text happen **on-device** in the Shortcut, so the
payload is usually just text (the raw image/audio can be attached for archival).

## How it runs

- Service: `uvicorn server:app` on `127.0.0.1:8787`, kept alive by
  `infra/launchd/com.brains.capture.plist`.
- Public front door: the Tailscale Funnel already forwards
  `https://anthonys-macbook-pro.tailc008aa.ts.net/` → `127.0.0.1:8787`, so the
  phone can POST from any network (no Tailscale app needed on the phone).
- Auth: every POST needs `X-Brain-Key: <CAPTURE_KEY>` (Keychain service `brain`,
  key `CAPTURE_KEY`). The endpoint is public, so the key is mandatory; it only
  *writes* captures (no read/delete), so a leaked key's blast radius is spam.
- Each capture → a `~/brains-ingest/capture/<ts>-<slug>.md` page (attachments
  under `capture/files/`), then a background `gbrain import + embed --stale` makes
  it queryable within seconds. The daily feeds-cron is the backstop.

```
GET  /health                      → {"ok": true, "key_configured": true}
POST /capture  (X-Brain-Key)      JSON {text, source?}  OR  multipart {text, source?, file}
```

To read the key for the Shortcut:
```bash
/Users/awvmeijer/gbrain/sidecars/.venv/bin/python -m keyring get brain CAPTURE_KEY
```

## iOS Shortcut — one core action, three modes

All three modes end in the same **Get Contents of URL** action:

- **URL:** `https://anthonys-macbook-pro.tailc008aa.ts.net/capture`
- **Method:** `POST`
- **Headers:** `X-Brain-Key` → *(your CAPTURE_KEY)*
- **Request Body:** `JSON` → fields: `text` = *(the text variable)*, `source` = `phone`/`voice`/`image`

### A) Text (Share Sheet)
1. New Shortcut "→ Brain". ⓘ → **Show in Share Sheet ON**, types **Text, URLs**.
2. **Text** action = `Shortcut Input`.
3. **Get Contents of URL** (as above, `source: phone`, `text`: the Text var).
4. (optional) **Show Notification** "Captured ✓".
Use: long-press any message → Share → **→ Brain**.

### B) Voice (on-device dictation)
1. New Shortcut "→ Brain (voice)".
2. **Dictate Text** (on-device speech→text).
3. **Get Contents of URL** (`source: voice`, `text`: Dictated Text).
Add to Home Screen or Back-Tap for one-tap voice notes.

### C) Image / screenshot (on-device OCR)
1. New Shortcut "→ Brain (photo)". ⓘ → Share Sheet types **Images** (or add **Select Photos**).
2. **Extract Text from Image** (input = Shortcut Input / selected image).
3. **Get Contents of URL** (`source: image`, `text`: Extracted Text).
   - *Optional archival:* set Request Body to **Form**, add `text` (the OCR text),
     `source`=`image`, and a **File** field = the image — the receiver stores it
     under `capture/files/` and links it on the page.

## Test
```bash
KEY=$(/Users/awvmeijer/gbrain/sidecars/.venv/bin/python -m keyring get brain CAPTURE_KEY)
curl -X POST https://anthonys-macbook-pro.tailc008aa.ts.net/capture \
  -H "X-Brain-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"text":"hello from the phone","source":"phone"}'
```

> Keep the Shortcut private — it embeds the key in the header. This path replaces
> the iCloud `manual-capture` recipe (kept for when iCloud has room again).
