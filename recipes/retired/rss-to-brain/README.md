---
id: rss-to-brain
name: RSS-to-Brain (finance news feeds)
version: 0.1.0
description: The 7 finance RSS feeds from the archived ~/brain app → one digest page per feed per day. Stdlib parsing, no keys.
category: sense
requires: []
secrets: []
setup_time: 5 min
cost_estimate: "$0 (public RSS — no keys, no quota)"
---

# RSS-to-Brain (finance news)

Ports the archived `~/brain` app's RSS ingestion (`brain/ingest/rss.py` +
`config.toml [ingest.rss]`). Pulls each feed in `feeds.txt` (Bloomberg, WSJ,
FT, Seeking Alpha, Reuters, CNBC, MarketWatch) and digests the day's new
entries into **one page per feed per day** — headline + link + one-line
summary each.

**Volume control is the whole point.** In the old brain, one-episode-per-entry
RSS grew to 18.8k of 22k episodes and drowned the graph. Here the ceiling is
7 pages/day; re-runs rewrite today's page with a superset (never duplicate,
never lose lines).

- **Dedup**: stable id = `sha256(feed_url + "|" + (id|guid|link|title))[:24]`,
  byte-identical to the old `rss.py::_entry_id`. State (seen ids + recent-day
  snippets for superset rewrites) lives at `~/gbrain/logs/rss-state.json` —
  never inside `~/brains-ingest`.
- **No new deps**: httpx (already in the sidecars venv) + stdlib `xml.etree`
  with a regex `<item>` fallback for broken XML. feedparser is NOT required;
  if you ever want it anyway: `~/gbrain/sidecars/.venv/bin/pip install feedparser`.
- **Real browser UA** — the default python-urllib UA gets 403'd by Bloomberg/DJ.

## Run

```bash
~/gbrain/sidecars/.venv/bin/python recipes/rss-to-brain/collect.py ~/brains-ingest --max 50
gbrain import ~/brains-ingest --no-embed && gbrain embed --stale
```

Wired into `infra/feeds-cron.sh` alongside the other collectors, so the daily
digest (`gbrain think`) sees the headlines too. Edit `feeds.txt`
(`slug | name | url`) to add/remove feeds.

## Notes / follow-ups
- Reuters (`reutersagency.com`) has been flaky/discontinued at times; the
  collector logs it under `failed:` and carries on — swap the URL in
  `feeds.txt` if it dies for good.
- Entries with dates older than the 7-day state window (or undated/future)
  bucket under today's page, so every page the collector might rewrite is
  still rebuildable from state.
- Feed pages are digests (headline + one-liner). If a story matters, the
  daily `gbrain think` digest is where it should surface; don't turn this
  back into one-page-per-article.
