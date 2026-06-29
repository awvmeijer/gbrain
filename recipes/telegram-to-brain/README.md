# telegram-to-brain

Ingest Telegram channels you're subscribed to (e.g. paid scanner/alert channels)
into the brain — fully automatic and ToS-compliant.

**Why this is fine (and Discord scraping isn't):** Telegram ships an official
user-facing API (MTProto). Reading channels *your own account is subscribed to*
is a sanctioned use — the same API `my.telegram.org` hands every user. Contrast
Discord, where automating a user account is a bannable self-bot. Keep it
**read-only** and use your established account; don't send/spam through the API.

## One-time setup (your part)

1. **Get API credentials** — go to <https://my.telegram.org> → *API development
   tools* → create an app → note the `api_id` and `api_hash`.
2. **Store them** in Keychain (service `brain`):
   ```bash
   VPY=~/gbrain/sidecars/.venv/bin/python
   $VPY -m keyring set brain TELEGRAM_API_ID    # paste api_id
   $VPY -m keyring set brain TELEGRAM_API_HASH  # paste api_hash
   ```
3. **Log in once** (prompts for your phone number, the code Telegram texts you,
   and your 2FA password if set) — mints a reusable session:
   ```bash
   $VPY ~/gbrain/recipes/telegram-to-brain/login.py
   ```
4. **Pick channels** — edit `channels.txt` (already seeded with Fundamental /
   Technical Scanner). Run the collector once with the list empty to print every
   channel your account can see.

## Run

```bash
VPY=~/gbrain/sidecars/.venv/bin/python
$VPY ~/gbrain/recipes/telegram-to-brain/collect.py ~/brains-ingest --limit 50
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
```

After setup it runs unattended via the daily `infra/feeds-cron.sh`. Output is one
markdown page per channel-per-day (frontmatter `source: telegram`, `channel`,
`date`; each line tagged `HH:MM author: message`) — same shape as the Discord
recipe, so entities/timeline derive natively after import.
