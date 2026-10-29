#!/usr/bin/env python3
"""Interactively store Telegram api_id / api_hash into Keychain (service `brain`).

Avoids the `python -m keyring set` prompt + shell-variable footguns. The hash is
read hidden (getpass) so it never lands in shell history or the screen.

    <venv-python> recipes/telegram-to-brain/set_secrets.py
"""
import getpass
import sys

import keyring

SERVICE = "brain"


def main() -> None:
    api_id = input("Telegram api_id (the number): ").strip()
    if not api_id.isdigit():
        print(f"api_id should be all digits — got {api_id!r}. Aborting.", file=sys.stderr)
        sys.exit(1)
    api_hash = getpass.getpass("Telegram api_hash (hidden, paste + Enter): ").strip()
    if len(api_hash) < 16:
        print("api_hash looks too short. Aborting.", file=sys.stderr)
        sys.exit(1)
    keyring.set_password(SERVICE, "TELEGRAM_API_ID", api_id)
    keyring.set_password(SERVICE, "TELEGRAM_API_HASH", api_hash)
    print("Stored TELEGRAM_API_ID + TELEGRAM_API_HASH in Keychain. Now run login.py.")


if __name__ == "__main__":
    main()
