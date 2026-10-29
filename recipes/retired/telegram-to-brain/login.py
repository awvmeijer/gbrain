#!/usr/bin/env python3
"""One-time Telegram login → mints a reusable session string into Keychain.

Telegram's MTProto API is officially user-facing: you register an app at
https://my.telegram.org (API development tools) to get an `api_id` + `api_hash`,
then log in with YOUR account. Reading channels you're subscribed to this way is
a sanctioned use of the API (unlike a Discord self-bot). This script does the
interactive phone-code login ONCE and stores the resulting StringSession so the
collector can run unattended afterward.

Prereqs (run these first):
    brain-style secrets in Keychain service "brain":
      python -m keyring set brain TELEGRAM_API_ID   <your api_id>
      python -m keyring set brain TELEGRAM_API_HASH  <your api_hash>

Then run this in a terminal (it will prompt for your phone number, the code
Telegram texts you, and your 2FA password if set):
    sidecars/.venv/bin/python recipes/telegram-to-brain/login.py
"""
from __future__ import annotations

import sys

import keyring
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

SERVICE = "brain"


def main() -> None:
    api_id = keyring.get_password(SERVICE, "TELEGRAM_API_ID")
    api_hash = keyring.get_password(SERVICE, "TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print(
            "ERROR: set TELEGRAM_API_ID and TELEGRAM_API_HASH in Keychain first "
            "(get them from https://my.telegram.org → API development tools).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Empty StringSession → interactive login on first connect.
    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        me = client.get_me()
        session = client.session.save()
        keyring.set_password(SERVICE, "TELEGRAM_SESSION", session)
        who = getattr(me, "username", None) or getattr(me, "first_name", "?")
        print(f"OK — logged in as {who}; session stored in Keychain (brain/TELEGRAM_SESSION).")
        print("You can now run collect.py unattended. Re-run this only if the session is revoked.")


if __name__ == "__main__":
    main()
