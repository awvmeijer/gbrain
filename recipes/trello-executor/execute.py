#!/usr/bin/env python3
"""Trello executor (Sprint 8) — the first real *actor*. Reads APPROVED todo
proposals (the proposals-as-pages gate) and creates Trello cards with due dates,
then flips the proposal to `done` and stamps the card url. Propose → approve → act.

Secrets in Keychain (service `brain`, never printed): TRELLO_KEY, TRELLO_TOKEN,
TRELLO_LIST_ID (default target list). Without them this no-ops with a clear
message — nothing is created. Conflict rule (per plan): Trello wins status, brain
wins evidence/links.

A proposal is actionable when: type=proposal, action=create_todo, status=approved.
Dedup: once executed, status→done + trello_card set, so re-runs skip it.

Run: python recipes/trello-executor/execute.py [--dry-run]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
LOG = GBRAIN / "logs" / "trello.log"
API = "https://api.trello.com/1"
DRY = "--dry-run" in sys.argv


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"[trello] {msg}", flush=True)
    with open(LOG, "a") as f:
        f.write(f"[trello] {msg}\n")


def secret(name: str) -> str | None:
    """Read from Keychain (service `brain`) via the `security` CLI — never echoed."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "brain", "-a", name, "-w"],
                           capture_output=True, text=True, timeout=8)
        return r.stdout.strip() or None
    except Exception:
        return None


def gb(args: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def field(content: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip().strip("'\"") if m else ""


def trello_post(path: str, params: dict, key: str, token: str) -> dict | None:
    params = {**params, "key": key, "token": token}
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"  trello POST {path} failed: {str(e)[:100]}")
        return None


def approved_todos() -> list[dict]:
    out = []
    _, lst = gb(["list", "--type", "proposal", "--limit", "100"])
    for line in lst.splitlines():
        slug = line.split("\t")[0].strip()
        if not slug.startswith("proposals/"):
            continue
        _, content = gb(["get", slug])
        if field(content, "action") == "create_todo" and field(content, "status").lower() == "approved" \
                and not field(content, "trello_card"):
            out.append({"slug": slug, "content": content,
                        "title": field(content, "title") or slug.split("/")[-1],
                        "due": field(content, "due"), "list": field(content, "list")})
    return out


def mark_done(slug: str, content: str, card_url: str) -> None:
    c = re.sub(r"^status:.*$", "status: done", content, count=1, flags=re.MULTILINE)
    if "trello_card:" not in c:
        c = re.sub(r"^(---\n)", rf"\1trello_card: {card_url}\n", c, count=1) if c.startswith("---") else c
    gb(["put", slug, "--content", c])


def main() -> None:
    key, token = secret("TRELLO_KEY"), secret("TRELLO_TOKEN")
    default_list = secret("TRELLO_LIST_ID")
    todos = approved_todos()
    log(f"{len(todos)} approved todo proposal(s) ready")
    if not (key and token):
        log("TRELLO_KEY / TRELLO_TOKEN not in Keychain — no-op. Set them + TRELLO_LIST_ID:")
        log("  brain set-secret TRELLO_KEY <key> ; brain set-secret TRELLO_TOKEN <token> ; brain set-secret TRELLO_LIST_ID <id>")
        for t in todos:
            log(f"  WOULD create card: '{t['title']}' due={t['due'] or 'none'}")
        return
    made = 0
    for t in todos:
        idlist = t["list"] or default_list
        if not idlist:
            log(f"  skip '{t['title']}': no list id (set TRELLO_LIST_ID or proposal `list:`)"); continue
        if DRY:
            log(f"  WOULD create '{t['title']}' due={t['due']} list={idlist}"); continue
        params = {"idList": idlist, "name": t["title"]}
        if t["due"]:
            params["due"] = t["due"]
        card = trello_post("/cards", params, key, token)
        if card and card.get("url"):
            mark_done(t["slug"], t["content"], card["url"])
            made += 1
            log(f"  + card '{t['title']}' → {card['url']}")
    log(f"DONE — {made} card(s) created")


if __name__ == "__main__":
    main()
