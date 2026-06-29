---
id: discord-to-brain
name: Discord-to-Brain
version: 0.1.0
description: Discord channel messages flow into brain pages (per channel-per-day). Deterministic REST collector; GBrain enriches into entities/timeline.
category: sense
requires: []
secrets:
  - name: DISCORD_BOT_TOKEN
    description: Discord bot token (shared with the brain bot). Keychain service `brain`.
    where: https://discord.com/developers/applications — Bot → Reset Token. Invite the bot (read-only) to servers you want ingested.
setup_time: 10 min
cost_estimate: "$0 (Discord bot API is free)"
---

# Discord-to-Brain

Recent messages from the bot's accessible text channels become brain pages —
one markdown page per channel-per-UTC-day — so your Discord history is
searchable and synthesizable alongside everything else.

## Pattern (code for data, LLM for judgment)

- **Deterministic collector** (`collect.py`): reads the bot token from Keychain
  (service `brain`, key `DISCORD_BOT_TOKEN`), discovers the bot's guild text
  channels via the Discord REST API, fetches recent messages, and writes
  `discord/<channel>/<YYYY-MM-DD>.md` pages. No gateway connection, no
  interpretation. Idempotent: `gbrain import` dedups unchanged files by hash.
- **Enrichment** (GBrain): `gbrain import` + `embed` + the dream cycle derive
  entities, links, and timeline from the page content.

## Run

```bash
# 1. Collect → markdown (defaults: all guild text channels except #digest)
~/brain/.venv/bin/python recipes/discord-to-brain/collect.py ~/brains-ingest --limit 100
# 2. Import + embed (bge-m3, local)
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
# 3. Use it
gbrain query "what came up in discord?"
gbrain think "recurring themes in #general"
```

Schedule the two steps as a Minion cron job (Phase 3) for hands-off ingestion.

## Scope notes

- **`#digest` is skipped** (`DISCORD_INGEST_EXCLUDE=digest`) so the brain never
  re-ingests its own posted digests — a feedback loop. Override with
  `DISCORD_INGEST_CHANNELS=<id,id>` to pin specific channels.
- The bot only sees servers it has been **invited** to. To ingest the finance
  Discords you actually spend time in, invite the `Brain OS` bot (read-only) to
  those servers; the collector auto-discovers their text channels.
- Self-bots (reading via your personal account token) violate Discord ToS and
  are intentionally NOT supported.
