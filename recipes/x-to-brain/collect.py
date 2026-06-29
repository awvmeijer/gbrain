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
    .venv/bin/python collect.py <output_dir> [--per-handle-sleep 1.5] [--max 30]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
INSTANCES = ["https://nitter.net", "https://nitter.poast.org", "https://nitter.privacydev.net"]
HERE = Path(__file__).parent


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--per-handle-sleep", type=float, default=1.5)
    ap.add_argument("--max", type=int, default=30, help="max tweets/handle to keep")
    args = ap.parse_args()

    out_root = Path(args.output_dir)
    seen_path = out_root / "x" / ".seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}

    handles = _handles()
    written, ok_handles, dead = 0, 0, []
    with httpx.Client() as client:
        for handle in handles:
            rss = _fetch_rss(client, handle)
            if not rss:
                dead.append(handle)
                time.sleep(args.per_handle_sleep)
                continue
            ok_handles += 1
            items = _parse_items(rss)[: args.max]
            seen_links = set(seen.get(handle, []))
            by_day: dict[str, list[dict]] = defaultdict(list)
            for it in items:
                if it["link"] and it["link"] in seen_links:
                    continue
                by_day[it["day"] or "undated"].append(it)
                if it["link"]:
                    seen_links.add(it["link"])
            for day, tweets in by_day.items():
                lines = [f"- **{t['hm']}** {t['text']}" + (f"  ({t['link']})" if t["link"] else "") for t in tweets]
                body = (
                    f"---\ntitle: X @{handle} — {day}\nsource: x\nhandle: {handle}\n"
                    f"date: {day}\ntags: [x, twitter, finance, {handle.lower()}]\n---\n\n"
                    f"# X @{handle} — {day}\n\n" + "\n".join(lines) + "\n"
                )
                p = out_root / "x" / handle.lower() / f"{day}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
                written += 1
            seen[handle] = sorted(seen_links)
            time.sleep(args.per_handle_sleep)

    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, indent=2))
    print(f"handles ok: {ok_handles}/{len(handles)} | pages written: {written} | unreachable: {len(dead)}")
    if dead:
        print("unreachable handles (no live instance returned a feed):", ", ".join(dead[:20]))


if __name__ == "__main__":
    main()
