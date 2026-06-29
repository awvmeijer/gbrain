---
id: youtube-to-brain
name: YouTube-to-Brain (finance creators)
version: 0.1.0
description: Latest videos from a list of YouTube creators → transcript pages. FREE (no YouTube Data API). Daily digest via `gbrain think`.
category: sense
requires: []
secrets: []
setup_time: 10 min
cost_estimate: "$0 (channel page + RSS + youtube-transcript-api need no key/quota)"
---

# YouTube-to-Brain

Pulls the latest videos from the creators in `channels.txt`, fetches each
video's auto-caption transcript, and writes one page per video. Your finance
creators' calls become searchable + synthesizable; a daily digest is a
`gbrain think` over the recent youtube pages.

## Pattern (code for data, LLM for judgment)

- **Collector** (`collect.py`, deterministic, FREE): resolves each `@handle`
  → channel id (public channel page), reads the channel **RSS feed** for the
  latest videos, fetches transcripts via `youtube-transcript-api`, writes
  `youtube/<handle>/<video_id>.md`. Per-channel seen-set → re-runs fetch only
  new videos. No API key, no quota.
- **Digest** (LLM, on demand): `gbrain think` synthesizes across the recent
  transcript pages.

## Run

```bash
V=recipes/youtube-to-brain/.venv
python3 -m venv "$V" && "$V/bin/pip" install youtube-transcript-api httpx   # once
"$V/bin/python" recipes/youtube-to-brain/collect.py ~/brains-ingest --limit 1
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
gbrain think "Digest my finance YouTube creators' latest videos: themes, tickers, calls."
```

Edit `channels.txt` to add/remove creators. Schedule the collect+embed as a
Minion cron (Phase 3) for a hands-off daily digest.

## Known limitations / follow-ups
- **Thin digests**: `think` retrieves the top-scoring chunks (often the video
  intro), so cross-creator digests can miss mid-video analysis. Enhancement:
  a per-video summarization pass (feed each FULL transcript to the Max bridge —
  Claude's 200k context fits a whole transcript — and store a digest page).
  Tracked as a Phase-3 follow-up.
- **Channel resolution**: a handful of handles (e.g. `@ARKInvest2015`) don't
  expose the channel id in the canonical/externalId markers; add a fallback
  (parse `/videos` or a `ytInitialData` `browseId`) when needed.
- Transcripts capped at `--max-chars` (default 40k) to bound embed size.
