# manual-capture

The compliant **one-tap** path for content the brain can't auto-ingest — a message
in a Discord member-only room, a tweet, a paragraph from anywhere. You share it; it
lands in the brain. No self-bot, no server, no API key.

How it flows: an **iOS Shortcut** saves the shared text to `iCloud Drive/BrainCapture/`
→ iCloud syncs it to the Mac → the daily `feeds-cron` runs `import_captures.py`,
which wraps each file into a `source: capture` page and archives the original (so
the folder self-empties).

## Build the Shortcut (one time, ~2 min)

On the iPhone, open **Shortcuts** → **+** → add these actions in order:

1. **Receive** — tap the top bar → set *Receive* **Text** (and **URLs**, **Rich Text**)
   from **Share Sheet**. Toggle on **Show in Share Sheet**.
2. **Text** — set its value to the **Shortcut Input** (the shared content).
3. **Save File** —
   - Service: **iCloud Drive**
   - Destination: a folder named **BrainCapture** (create it once in the Files app
     under iCloud Drive, or let Save File create it)
   - **Ask Where to Save: OFF**
   - **Overwrite If File Exists: OFF** (each capture is a new file)
4. Name the Shortcut e.g. **→ Brain** and give it an icon.

Now in **Discord** (or anywhere): long-press a message → **Share** → **→ Brain**.
Done. It is imported on the next daily feeds run. (To make it search-able immediately, run the
import + embed manually — see below.)

> Tip: filename uniqueness — if Save File ever complains about a duplicate name,
> add a **Current Date** (formatted) action and append it to the file name.

## Mac side (already wired)

`feeds-cron.sh` runs this each day:

```bash
sidecars/.venv/bin/python recipes/manual-capture/import_captures.py ~/brains-ingest
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
```

Run those two lines by hand anytime to pull captures in immediately.

## Retired: the zero-Shortcut Discord alternative

This used to route forwarded Discord messages through `recipes/discord-to-brain`.
That collector retired with memory-only-v1 (2026-10-24; now under
`recipes/retired/`), so the Shortcut above, or a POST to `/capture`, is the way in.
