---
id: x-to-brain
name: X-to-Brain (fintwit, FREE via Nitter)
version: 0.1.0
description: Recent tweets from a fintwit handle list → brain pages, via public Nitter RSS (no paid X API). Daily digest via `gbrain think`.
category: sense
requires: []
secrets: []
setup_time: 10 min
cost_estimate: "$0 (Nitter RSS — no X API key/quota)"
---

# X-to-Brain (fintwit)

Pulls recent tweets for each handle in `handles.txt` from a public **Nitter**
instance's RSS feed (multi-instance failover; public Nitter is volatile), groups
them per handle-per-day into pages. Your fintwit follows become searchable +
synthesizable; the daily digest is a `gbrain think` over the recent x pages.

`handles.txt` is a snapshot of the user's forge `fintwit_handles.yaml`
(user-curated). Mention **volume is never a trade signal** — this is context
capture, not a buy/sell trigger.

## Run

```bash
# brain venv has httpx; no extra deps needed
~/brain/.venv/bin/python recipes/x-to-brain/collect.py ~/brains-ingest --per-handle-sleep 1.2 --max 25
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
gbrain think "Fintwit digest: tickers, catalysts, themes getting attention; name the handles."
```

Edit `handles.txt` to add/remove follows. Schedule collect+embed as a Minion
cron (Phase 3) for a hands-off daily digest.

## Notes / follow-ups
- **Nitter volatility**: instances die often. The collector fails over across
  `nitter.net → nitter.poast.org → nitter.privacydev.net`; as of last run only
  `nitter.net` was live (53/54 handles succeeded). Add fresh instances to
  `INSTANCES` in `collect.py` as they come/go. If all die, the fallback free
  path is scraping the logged-in X session via gstack `/browse`.
- Retweets (`RT by …`) are skipped to cut noise.
- `chatgpt21` returned no feed (likely renamed/suspended) — prune or fix.
