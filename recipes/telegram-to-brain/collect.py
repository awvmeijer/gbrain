"""Telegram → GBrain collector (deterministic; the recipe's "code for data" half).

Reads recent messages from the channels listed in channels.txt (matched by title
against the account's subscribed dialogs), groups them per channel-per-day, and
writes markdown pages into an output dir that `gbrain import` turns into pages.

Telegram's MTProto API is officially user-facing — reading channels you're
subscribed to via your own account is sanctioned (unlike a Discord self-bot).
Auth is the StringSession minted by login.py.

- Secrets: Keychain service `brain` — TELEGRAM_API_ID, TELEGRAM_API_HASH,
  TELEGRAM_SESSION (run login.py once to create the session). Never printed.
- Channels: channels.txt (one case-insensitive title substring per line;
  `#` comments allowed). Matched against the account's joined channels, so
  private/invite-only channels work without needing a public @username.
- Read-only. Idempotent: per (channel, UTC date) markdown file; re-runs overwrite
  the recent window and `gbrain import` dedups unchanged files by content hash.

Usage:
    sidecars/.venv/bin/python collect.py <output_dir> [--limit 50]
    # with no channels.txt match, prints the channels your account can see (to populate the file)
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import timezone
from pathlib import Path

import keyring
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

SERVICE = "brain"


def _secrets() -> tuple[int, str, str]:
    api_id = keyring.get_password(SERVICE, "TELEGRAM_API_ID")
    api_hash = keyring.get_password(SERVICE, "TELEGRAM_API_HASH")
    session = keyring.get_password(SERVICE, "TELEGRAM_SESSION")
    if not (api_id and api_hash and session):
        print(
            "ERROR: missing Telegram secrets in Keychain. Set TELEGRAM_API_ID / "
            "TELEGRAM_API_HASH and run login.py once to create TELEGRAM_SESSION.",
            file=sys.stderr,
        )
        sys.exit(1)
    return int(api_id), api_hash, session


def _wanted(recipe_dir: Path) -> list[str]:
    f = recipe_dir / "channels.txt"
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line.lower())
    return out


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (s or "").lower()).strip("-") or "x"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    api_id, api_hash, session = _secrets()
    recipe_dir = Path(__file__).resolve().parent
    wanted = _wanted(recipe_dir)

    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    written = 0
    total_msgs = 0
    with TelegramClient(StringSession(session), api_id, api_hash) as client:
        channels = [d for d in client.iter_dialogs() if d.is_channel]

        if not wanted:
            print("No channels.txt entries. Channels your account can see:")
            for d in channels:
                print(f"  - {d.name}")
            print("\nAdd the titles (or substrings) you want to channels.txt, one per line.")
            return

        targets = [d for d in channels if any(w in (d.name or "").lower() for w in wanted)]
        print(f"matched channels: {[d.name for d in targets]}")

        for d in targets:
            by_day: dict[str, list[dict]] = defaultdict(list)
            for m in client.iter_messages(d.entity, limit=args.limit):
                content = (m.message or "").strip()
                if not content:  # skip media-only / service messages
                    continue
                ts = m.date.astimezone(timezone.utc)
                author = getattr(m, "post_author", None) or (d.name or "channel")
                by_day[ts.strftime("%Y-%m-%d")].append({"ts": ts, "author": author, "content": content})
                total_msgs += 1

            cname = _slug(d.name)
            for day, items in by_day.items():
                items.sort(key=lambda x: x["ts"])
                lines = [f"- **{i['ts'].strftime('%H:%M')}** {i['author']}: {i['content']}" for i in items]
                body = (
                    f"---\n"
                    f"title: Telegram {d.name} — {day}\n"
                    f"source: telegram\n"
                    f"channel: {d.name}\n"
                    f"date: {day}\n"
                    f"tags: [telegram, chat, finance]\n"
                    f"---\n\n"
                    f"# Telegram {d.name} — {day}\n\n"
                    + "\n".join(lines)
                    + "\n"
                )
                path = out_root / "telegram" / cname / f"{day}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
                written += 1

    print(f"messages fetched: {total_msgs} | pages written: {written} | output: {out_root}")


if __name__ == "__main__":
    main()
