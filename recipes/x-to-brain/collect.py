"""X/Twitter → GBrain collector (deterministic; FREE via Nitter RSS).

For each handle in handles.txt, fetches recent tweets from a public Nitter
instance's RSS feed (<instance>/<handle>/rss), trying instances in order until
one parses (public Nitter is volatile). Groups tweets per handle-per-day into
markdown pages. No paid X API, no key.

Snapshot of the user's forge fintwit handles; mirrors forge's nitter_rss
adapter approach (multi-instance failover). Per spec: mention VOLUME is never a
trade signal — this is signal/context capture, not a buy/sell trigger.

State: per-handle seen tweet-link set in <out>/x/.seen.json.

Usage:
    .venv/bin/python collect.py <output_dir> [--per-handle-sleep 1.5] [--max 30] [--heal]

--heal (used by feeds-cron): probe + rewrite instances.txt first (see
heal_instances.py), re-probe and retry unreachable handles once if coverage is
under --retry-below, and lift the per-handle cap after an outage (backfill).
Run state for the feeds watchdog: ~/gbrain/logs/x-collect-state.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))  # heal_instances sibling

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
HERE = Path(__file__).parent

# Nitter instances, newest-known-good first. 2026-08-21: the three hardcoded
# here (nitter.net, nitter.poast.org, nitter.privacydev.net) all died within a
# day of each other and X ingestion was dark for 30 nights before anyone noticed
# — the list being CODE meant the fix needed an edit here, and nothing was
# watching. It is now a data file: when an instance dies, edit instances.txt.
# Public Nitter is volunteer-run and mortal; assume every entry is temporary.
DEFAULT_INSTANCES = ["https://shitter.thepixora.com"]


def _instances() -> list[str]:
    """Instance list: $X_NITTER_INSTANCES (comma-sep) > instances.txt > default."""
    env = os.environ.get("X_NITTER_INSTANCES", "").strip()
    if env:
        return [u.strip().rstrip("/") for u in env.split(",") if u.strip()]
    f = HERE / "instances.txt"
    if f.is_file():
        out = []
        for line in f.read_text().splitlines():
            s = line.split("#", 1)[0].strip().rstrip("/")
            if s:
                out.append(s if "://" in s else f"https://{s}")
        if out:
            return out
    return DEFAULT_INSTANCES


INSTANCES = _instances()


def _handles() -> list[str]:
    out = []
    for line in (HERE / "handles.txt").read_text().splitlines():
        s = line.strip().lstrip("@")
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _unescape(s: str) -> str:
    return (
        s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&#39;", "'").replace("<![CDATA[", "").replace("]]>", "").strip()
    )


def _fetch_rss(client: httpx.Client, handle: str) -> str | None:
    for inst in INSTANCES:
        try:
            r = client.get(f"{inst}/{handle}/rss", headers=UA, timeout=15, follow_redirects=True)
            if r.status_code == 200 and "<item>" in r.text:
                return r.text
        except Exception:  # noqa: BLE001 — try next instance
            continue
    return None


def _parse_items(rss: str) -> list[dict]:
    items = []
    for block in re.findall(r"<item>(.*?)</item>", rss, re.DOTALL):
        title = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
        link = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL)
        text = _unescape(title.group(1)) if title else ""
        if not text or text.lower().startswith("rt by "):  # skip retweets (noise)
            continue
        try:
            dt = parsedate_to_datetime(pub.group(1).strip()) if pub else None
            day = dt.strftime("%Y-%m-%d") if dt else ""
            hm = dt.strftime("%H:%M") if dt else ""
        except Exception:  # noqa: BLE001
            day, hm = "", ""
        items.append({"text": text, "link": (link.group(1).strip() if link else ""), "day": day, "hm": hm})
    return items


LOGS = HERE.parent.parent / "logs"
RUN_STATE = LOGS / "x-collect-state.json"


def _write_day(out_root: Path, handle: str, day: str, tweets: list[dict]) -> bool:
    """Write/merge one handle-day page. Merges into an existing page: the seen-set
    already filters repeats, so overwriting (the old behaviour) silently dropped
    that day's earlier tweets whenever a second run that day found new ones."""
    lines = [f"- **{t['hm']}** {t['text']}" + (f"  ({t['link']})" if t["link"] else "") for t in tweets]
    p = out_root / "x" / handle.lower() / f"{day}.md"
    if p.exists():
        existing = p.read_text()
        fresh = [ln for ln in lines if ln not in existing]
        if not fresh:
            return False
        p.write_text(existing.rstrip("\n") + "\n" + "\n".join(fresh) + "\n")
        return True
    body = (
        f"---\ntitle: X @{handle} — {day}\nsource: x\nhandle: {handle}\n"
        f"date: {day}\ntags: [x, twitter, finance, {handle.lower()}]\n---\n\n"
        f"# X @{handle} — {day}\n\n" + "\n".join(lines) + "\n"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return True


def _collect(client: httpx.Client, handles: list[str], seen: dict, out_root: Path,
             cap: int, sleep: float) -> tuple[int, list[str], set[str]]:
    """Fetch + write each handle. Returns (pages written, unreachable, days written)."""
    written, dead, days = 0, [], set()
    for handle in handles:
        rss = _fetch_rss(client, handle)
        if not rss:
            dead.append(handle)
            time.sleep(sleep)
            continue
        items = _parse_items(rss)[:cap]
        seen_links = set(seen.get(handle, []))
        by_day: dict[str, list[dict]] = defaultdict(list)
        for it in items:
            if it["link"] and it["link"] in seen_links:
                continue
            by_day[it["day"] or "undated"].append(it)
            if it["link"]:
                seen_links.add(it["link"])
        for day, tweets in by_day.items():
            if _write_day(out_root, handle, day, tweets):
                written += 1
                days.add(day)
        seen[handle] = sorted(seen_links)
        time.sleep(sleep)
    return written, dead, days


def _heal(force_status: bool = True) -> str:
    """Probe + rewrite instances.txt, then reload INSTANCES. Never raises."""
    global INSTANCES
    try:
        import heal_instances
        s = heal_instances.heal(use_status=force_status)
        INSTANCES = _instances()
        return heal_instances.one_line(s)
    except Exception as e:  # noqa: BLE001 — healing must never block collection
        return f"x-instances: heal failed ({type(e).__name__}: {e})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--per-handle-sleep", type=float, default=1.5)
    ap.add_argument("--max", type=int, default=30, help="max tweets/handle to keep")
    ap.add_argument("--heal", action="store_true",
                    help="probe + rewrite instances.txt before collecting, and re-probe"
                         " + retry unreachable handles once if coverage is low")
    ap.add_argument("--retry-below", type=float, default=0.5,
                    help="with --heal: re-probe + retry when handles-ok ratio is below this")
    args = ap.parse_args()

    out_root = Path(args.output_dir)
    seen_path = out_root / "x" / ".seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}
    try:
        state = json.loads(RUN_STATE.read_text())
    except (OSError, ValueError):
        state = {}

    notes = []
    if args.heal:
        notes.append(_heal())

    # Backfill on recovery: Nitter RSS serves only the newest ~20 tweets per
    # handle (no paging), so after an outage the reachable window is whatever
    # those items span. Lift the per-handle cap so nothing inside it is dropped.
    today = datetime.now().strftime("%Y-%m-%d")
    last_ok = state.get("last_success_day", "")
    cap = args.max
    gap_days = 0
    if last_ok:
        gap_days = (datetime.fromisoformat(today) - datetime.fromisoformat(last_ok)).days
        if gap_days > 1:
            cap = max(cap, 200)
            notes.append(f"recovery: last good X day {last_ok} ({gap_days}d ago) — per-handle cap lifted")

    handles = _handles()
    with httpx.Client() as client:
        written, dead, days = _collect(client, handles, seen, out_root, cap, args.per_handle_sleep)
        ratio = (len(handles) - len(dead)) / max(len(handles), 1)
        if args.heal and dead and ratio < args.retry_below:
            notes.append(f"low coverage {len(handles) - len(dead)}/{len(handles)} — re-probing")
            notes.append(_heal())
            w2, dead, d2 = _collect(client, dead, seen, out_root, cap, args.per_handle_sleep)
            written += w2
            days |= d2
            notes.append(f"retry recovered {w2} page(s)")

    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, indent=2))
    ok_handles = len(handles) - len(dead)
    ratio = ok_handles / max(len(handles), 1)
    # Days inside the outage window that this run filled (pinned years-old
    # tweets also arrive undeduped on a fresh seen-set; they are not backfill).
    backfilled = sorted(d for d in days if d != "undated" and last_ok <= d < today)
    state.update({
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "handles_ok": ok_handles, "handles_total": len(handles),
        "pages_written": written, "unreachable": dead,
        "backfilled_days": backfilled if gap_days > 1 else [],
    })
    if ratio >= args.retry_below:
        state["last_success_day"] = today
    LOGS.mkdir(parents=True, exist_ok=True)
    RUN_STATE.write_text(json.dumps(state, indent=2))

    for n in notes:
        print(n)
    print(f"handles ok: {ok_handles}/{len(handles)} | pages written: {written} | unreachable: {len(dead)}")
    if dead:
        print("unreachable handles (no live instance returned a feed):", ", ".join(dead[:20]))


if __name__ == "__main__":
    main()
