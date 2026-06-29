"""Discord → GBrain collector (deterministic; the recipe's "code for data" half).

Reads recent messages from the brain bot's accessible text channels via the
Discord REST API (no gateway connection, no discord.py), groups them per
channel-per-day, and writes markdown pages into an output dir that
`gbrain import` then turns into pages. The agent/enrichment half (entity
extraction, timeline) is GBrain's job after import.

- Token: Keychain service `brain`, key `DISCORD_BOT_TOKEN` (shared with the
  brain bot). Never printed.
- Channels: auto-discovers the bot's guild text channels; skips any in
  DISCORD_INGEST_EXCLUDE (default "digest") to avoid re-ingesting the brain's
  own posted digests (feedback loop). Override with DISCORD_INGEST_CHANNELS
  (comma-separated channel IDs).
- Idempotent: per (channel, UTC date) markdown file; re-runs overwrite the
  recent window, and `gbrain import` dedups unchanged files by content hash.

Usage:
    python collect.py <output_dir> [--limit 100]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import keyring

API = "https://discord.com/api/v10"
SERVICE = "brain"


def _token() -> str:
    tok = keyring.get_password(SERVICE, "DISCORD_BOT_TOKEN")
    if not tok:
        print("ERROR: no DISCORD_BOT_TOKEN in keychain (service 'brain').", file=sys.stderr)
        sys.exit(1)
    return tok


def _headers(tok: str) -> dict:
    return {"Authorization": f"Bot {tok}", "User-Agent": "brains-gbrain-discord/0.1"}


def _discover_channels(client: httpx.Client, h: dict, exclude: set[str]) -> list[dict]:
    """All GUILD_TEXT/announcement channels the bot can see, minus excluded names."""
    out: list[dict] = []
    guilds = client.get(f"{API}/users/@me/guilds", headers=h).json()
    for g in guilds if isinstance(guilds, list) else []:
        gid = g.get("id")
        chans = client.get(f"{API}/guilds/{gid}/channels", headers=h).json()
        for c in chans if isinstance(chans, list) else []:
            if c.get("type") in (0, 5) and (c.get("name") or "").lower() not in exclude:
                out.append({"id": c["id"], "name": c.get("name"), "guild": gid, "guild_name": g.get("name")})
    return out


def _fetch_messages(client: httpx.Client, h: dict, channel_id: str, limit: int) -> list[dict]:
    r = client.get(f"{API}/channels/{channel_id}/messages", headers=h, params={"limit": limit})
    if r.status_code != 200:
        print(f"  channel {channel_id}: messages {r.status_code} {r.text[:120]}", file=sys.stderr)
        return []
    msgs = r.json()
    return list(reversed(msgs)) if isinstance(msgs, list) else []  # oldest-first


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (s or "").lower()).strip("-") or "x"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    tok = _token()
    h = _headers(tok)
    exclude = {x.strip().lower() for x in os.environ.get("DISCORD_INGEST_EXCLUDE", "digest").split(",") if x.strip()}
    forced = [x.strip() for x in os.environ.get("DISCORD_INGEST_CHANNELS", "").split(",") if x.strip()]

    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    written = 0
    total_msgs = 0
    with httpx.Client(timeout=30.0) as client:
        if forced:
            channels = [{"id": cid, "name": cid, "guild": None, "guild_name": None} for cid in forced]
        else:
            channels = _discover_channels(client, h, exclude)
        print(f"channels: {[c['name'] for c in channels]}")

        for ch in channels:
            msgs = _fetch_messages(client, h, ch["id"], args.limit)
            total_msgs += len(msgs)
            # group by UTC date
            by_day: dict[str, list[dict]] = defaultdict(list)
            for m in msgs:
                content = (m.get("content") or "").strip()
                if not content:  # skip embeds/attachments-only system messages
                    continue
                ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
                by_day[ts.strftime("%Y-%m-%d")].append({"ts": ts, "author": (m.get("author") or {}).get("global_name") or (m.get("author") or {}).get("username") or "?", "content": content, "id": m["id"]})

            cname = _slug(ch["name"])
            for day, items in by_day.items():
                items.sort(key=lambda x: x["ts"])
                lines = [f"- **{i['ts'].strftime('%H:%M')}** {i['author']}: {i['content']}" for i in items]
                body = (
                    f"---\n"
                    f"title: Discord #{ch['name']} — {day}\n"
                    f"source: discord\n"
                    f"channel: {ch['name']}\n"
                    f"date: {day}\n"
                    f"tags: [discord, chat]\n"
                    f"---\n\n"
                    f"# Discord #{ch['name']} — {day}\n\n"
                    + "\n".join(lines)
                    + "\n"
                )
                path = out_root / "discord" / cname / f"{day}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
                written += 1

    print(f"messages fetched: {total_msgs} | pages written: {written} | output: {out_root}")


if __name__ == "__main__":
    main()
