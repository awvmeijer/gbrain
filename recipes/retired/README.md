# Retired recipes

Brains has been memory only since memory-only-v1 (2026-10-24, `fca065fd6`). These
collectors and synthesis jobs were unscheduled then, and Breakwise now collects its own
market sources. They are kept here for reference only. Nothing schedules them,
and their paths (some resolve `~/gbrain` from `__file__`) are no longer maintained.

| Recipe | Was |
|---|---|
| `x-to-brain/` | fintwit X timelines via Nitter RSS |
| `youtube-to-brain/` | YouTube transcripts + backfill |
| `rss-to-brain/` | curated finance RSS feeds |
| `edgar-to-brain/` | SEC EDGAR filings for watchlist tickers |
| `telegram-to-brain/` | Telegram scanner channels |
| `discord-to-brain/` | Discord bot channel collector |
| `federal-to-brain/` | USAspending contracts/grants + openFDA, mapped to watchlist tickers |
| `convergence/` | cross-source "1+1=3" findings engine |
| `creator-scorecard/` | weekly creator call scorecard |
| `fintwit-analyst/` | the daily digest |

The upstream `recipes/x-to-brain.md` integration manifest stays where it is:
`gbrain integrations` and its tests read it.

What still runs lives in `infra/feeds-cron.sh` (git, prs, obsidian, captures,
hermes) and `infra/dream-cron.sh` (dream, entity-extract).
