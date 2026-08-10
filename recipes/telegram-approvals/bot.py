#!/usr/bin/env python3
"""Telegram approvals bot — the phone-side human-approval surface.

Deterministic by design: approvals NEVER route through an LLM. This bot
long-polls Telegram and does exactly three things:

  1. /needs  — list pending proposals with inline ✅ Approve / ❌ Reject buttons
  2. /brief  — send the latest daily digest (brains-ingest/digests)
  3. 07:30 daily — proactively push the digest + pending proposals

Button taps POST /api/decide on localhost with decided_by=telegram. The bot is
outbound-only (long-polling) — no funnel, no inbound port, works from anywhere.

Auth: only TELEGRAM_ALLOWED_USER_ID may talk to it — enforced on BOTH message
`from.id` and callback_query `from.id` (a leaked bot username is harmless).

Secrets: ~/.gbrain/telegram_approvals.env (0600), provisioned by
recipes/hermes-bridge/setup.sh from Keychain service `brain`. launchd jobs
must never read the Keychain directly (post-sleep -25320 lock trap).

Run: launchd com.brains.telegram-approvals.plist (KeepAlive), or foreground:
    ~/gbrain/sidecars/.venv/bin/python ~/gbrain/recipes/telegram-approvals/bot.py
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
ENV_FILE = HOME / ".gbrain" / "telegram_approvals.env"
KEY_FILE = HOME / ".gbrain" / "client.key"
BRAIN = "http://127.0.0.1:8787"
UA = "brains-approvals-bot/1.0"  # Discord 403s default urllib UA; be explicit everywhere
BRIEF_HOUR, BRIEF_MIN = 7, 30
STATE = HOME / ".gbrain" / "telegram_approvals.state.json"  # offset + last brief date


def _env() -> dict:
    out = {}
    if ENV_FILE.exists():
        for ln in ENV_FILE.read_text().splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    return out


CFG = _env()
TOKEN = CFG.get("TELEGRAM_APPROVALS_BOT_TOKEN", "")
ALLOWED = {int(x) for x in CFG.get("TELEGRAM_ALLOWED_USER_ID", "").replace(",", " ").split() if x.strip().isdigit()}
API = f"https://api.telegram.org/bot{TOKEN}"


def log(msg: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def tg(method: str, **params) -> dict:
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.loads(r.read())


def brain(method: str, path: str, payload: dict | None = None) -> dict:
    key = KEY_FILE.read_text().strip()
    url = f"{BRAIN}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Brain-Key": key, "Content-Type": "application/json",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"offset": 0, "last_brief": "", "cb": {}}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st))


def send(chat_id: int, text: str, markup: dict | None = None) -> None:
    for chunk_start in range(0, max(len(text), 1), 3900):  # Telegram 4096-char cap
        chunk = text[chunk_start:chunk_start + 3900]
        params = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if markup and chunk_start + 3900 >= len(text):  # buttons on the last chunk
            params["reply_markup"] = markup
        tg("sendMessage", **params)


def needs_items() -> list[dict]:
    return brain("GET", "/api/needs").get("proposals", [])


def send_needs(chat_id: int, st: dict, header: str = "") -> None:
    props = needs_items()
    if not props:
        if header:
            send(chat_id, header + "\n\nNothing needs you. ✨")
        else:
            send(chat_id, "Nothing needs you. ✨")
        return
    if header:
        send(chat_id, header)
    for p in props:
        # callback_data is capped at 64 bytes → map short ids to slugs in state
        cid = str(int(time.time() * 1000) % 10**9)
        st.setdefault("cb", {})[cid] = p["slug"]
        text = (f"⚖️ {p['title']}\n"
                f"action: {p['action']} → {p['target']}\n"
                f"why: {p['rationale']}\n"
                f"undo: {p.get('rollback', '')}")
        markup = {"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"a:{cid}"},
            {"text": "❌ Reject", "callback_data": f"r:{cid}"},
        ]]}
        send(chat_id, text, markup)
    # keep the callback map from growing without bound
    if len(st["cb"]) > 200:
        for k in sorted(st["cb"])[:-100]:
            del st["cb"][k]
    save_state(st)


def send_brief(chat_id: int) -> None:
    today = brain("GET", "/api/today")
    content = (today.get("content") or "").strip()
    date = today.get("date") or datetime.now().strftime("%Y-%m-%d")
    if content:
        send(chat_id, f"☀️ Briefing — {date}\n\n{content}")
    else:
        send(chat_id, "No digest yet today.")


def handle_callback(cb: dict, st: dict) -> None:
    uid = cb.get("from", {}).get("id")
    if uid not in ALLOWED:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text="Not authorized.")
        return
    action, _, cid = (cb.get("data") or "").partition(":")
    slug = st.get("cb", {}).get(cid)
    if action not in ("a", "r") or not slug:
        tg("answerCallbackQuery", callback_query_id=cb["id"],
           text="Expired — send /needs for a fresh list.")
        return
    decision = "approve" if action == "a" else "reject"
    try:
        res = brain("POST", "/api/decide",
                    {"slug": slug, "decision": decision, "decided_by": "telegram"})
        verdict = "✅ approved" if decision == "approve" else "❌ rejected"
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=f"{verdict}")
        msg = cb.get("message") or {}
        if msg:
            tg("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
               text=(msg.get("text") or slug) + f"\n\n→ {verdict} ({res.get('slug', slug)})")
        log(f"decide {decision} {slug} by uid={uid}")
    except Exception as e:  # noqa: BLE001
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=f"Failed: {e}"[:190])
        log(f"decide failed for {slug}: {e}")


def handle_message(m: dict, st: dict) -> None:
    uid = m.get("from", {}).get("id")
    chat_id = m.get("chat", {}).get("id")
    if uid not in ALLOWED:
        log(f"refused message from uid={uid}")
        return
    text = (m.get("text") or "").strip().lower()
    if text.startswith("/needs") or text.startswith("/start"):
        send_needs(chat_id, st)
    elif text.startswith("/brief"):
        send_brief(chat_id)
    else:
        send(chat_id, "Commands: /needs — pending approvals · /brief — today's briefing\n"
                      "(For conversation, talk to the Hermes bot.)")


def maybe_morning_push(st: dict) -> None:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if st.get("last_brief") == today or not ALLOWED:
        return
    target = now.replace(hour=BRIEF_HOUR, minute=BRIEF_MIN, second=0, microsecond=0)
    if now < target or now > target + timedelta(hours=6):  # missed window (Mac asleep) → skip after 13:30
        if now > target + timedelta(hours=6):
            st["last_brief"] = today
            save_state(st)
        return
    chat_id = next(iter(ALLOWED))  # single-user bot: user id == private chat id
    try:
        send_brief(chat_id)
        send_needs(chat_id, st, header="⚖️ Needs you:")
        st["last_brief"] = today
        save_state(st)
        log("morning push sent")
    except Exception as e:  # noqa: BLE001
        log(f"morning push failed: {e}")


def main() -> None:
    if not TOKEN or not ALLOWED:
        raise SystemExit(f"missing TELEGRAM_APPROVALS_BOT_TOKEN / TELEGRAM_ALLOWED_USER_ID in {ENV_FILE} "
                         "— run recipes/hermes-bridge/setup.sh")
    st = load_state()
    log(f"approvals bot up (allowed={sorted(ALLOWED)})")
    while True:
        try:
            maybe_morning_push(st)
            updates = tg("getUpdates", offset=st.get("offset", 0) + 1, timeout=50,
                         allowed_updates=["message", "callback_query"])
            for u in updates.get("result", []):
                st["offset"] = max(st.get("offset", 0), u["update_id"])
                if "callback_query" in u:
                    handle_callback(u["callback_query"], st)
                elif "message" in u:
                    handle_message(u["message"], st)
                save_state(st)
        except Exception as e:  # noqa: BLE001 — network blips must not kill the loop
            log(f"poll error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
