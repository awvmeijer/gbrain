"""Finance RSS → GBrain collector (deterministic; ports the archived ~/brain RSS ingest).

For each feed in feeds.txt, fetches the RSS/Atom feed and digests that day's
NEW entries into ONE markdown page per feed per day (headline + link + one-line
summary each). The old brain wrote one episode per entry and RSS ended up as
18.8k of 22k episodes — the per-day digest page is the volume control.

- Dedup: stable id = sha256(feed_url + "|" + (entry id|guid|link|title)),
  mirroring ~/brain/brain/ingest/rss.py::_entry_id. An entry is never written
  twice; state lives in ~/gbrain/logs/rss-state.json (edgar/federal/science
  convention — never inside ~/brains-ingest).
- Superset rewrites: recent-day entry snippets are kept in the state file, so a
  later run rewrites today's page with old+new entries (never loses lines).
  Entries dated older than DAYS_KEPT (or in the future) bucket under today, so
  every day page we might touch is still rebuildable from state.
- Parsing: stdlib xml.etree first (handles RSS 2.0 + Atom), regex <item>
  fallback for feeds that serve slightly broken XML (x-to-brain idiom).
  No feedparser dependency; a real browser UA (python-urllib UAs get 403'd).

Usage:
    python collect.py <output_dir> [--max 50]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import httpx

HOME = Path.home()
STATE = HOME / "gbrain" / "logs" / "rss-state.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
HERE = Path(__file__).parent
DAYS_KEPT = 7      # days of entry snippets kept in state for superset rewrites
SEEN_CAP = 5000    # per-feed dedup-id cap (oldest ids dropped first)
SUMMARY_LEN = 200  # one-line summary truncation


def _feeds() -> list[dict[str, str]]:
    out = []
    for line in (HERE / "feeds.txt").read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        slug, name, url = (p.strip() for p in s.split("|", 2))
        out.append({"slug": slug, "name": name, "url": url})
    return out


def _clean(html: str | None) -> str:
    """HTML → single-line text: strip tags/CDATA, unescape entities, collapse ws."""
    if not html:
        return ""
    text = html.replace("<![CDATA[", "").replace("]]>", "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _entry_id(feed_url: str, entry: dict) -> str:
    # Mirrors ~/brain/brain/ingest/rss.py::_entry_id (same hash → same identity
    # for anything the legacy-replay slice already carried over).
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha256(f"{feed_url}|{raw}".encode()).hexdigest()[:24]


def _parse_ts(val: str | None) -> datetime | None:
    if not val:
        return None
    val = val.strip()
    try:  # RFC 822 (RSS pubDate)
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(val)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        pass
    try:  # ISO 8601 (Atom published/updated)
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _items_etree(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        f: dict = {}
        for c in el:
            t = _local(c.tag)
            if t == "link":
                href = (c.get("href") or c.text or "").strip()
                if href and c.get("rel") in (None, "alternate"):
                    f.setdefault("link", href)
            elif t in ("guid", "id"):
                f["id"] = (c.text or "").strip()
            elif t == "title":
                f["title"] = _clean("".join(c.itertext()))
            elif t in ("description", "summary", "encoded", "content"):
                f.setdefault("summary", _clean("".join(c.itertext())))
            elif t in ("pubDate", "published", "updated", "date"):
                f.setdefault("ts", _parse_ts(c.text))
        if f.get("title"):
            items.append(f)
    return items


def _items_regex(xml_text: str) -> list[dict]:
    """Fallback for broken XML: RSS 2.0 <item> blocks only (x-to-brain idiom)."""
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL):
        def grab(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.DOTALL)
            return m.group(1).strip() if m else ""
        f = {
            "title": _clean(grab("title")),
            "link": _clean(grab("link")),
            "id": _clean(grab("guid")),
            "summary": _clean(grab("description")),
            "ts": _parse_ts(_clean(grab("pubDate"))),
        }
        if f["title"]:
            items.append(f)
    return items


def _bucket_day(ts: datetime | None, now: datetime) -> tuple[str, str]:
    """(day, HH:MM) for an entry — old/undated/future entries bucket under today
    so every page we might rewrite stays within the state window (superset-safe)."""
    if ts is None:
        return now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    ts = ts.astimezone(timezone.utc)
    if ts > now + timedelta(hours=1) or ts < now - timedelta(days=DAYS_KEPT - 1):
        return now.strftime("%Y-%m-%d"), ts.strftime("%H:%M")
    return ts.strftime("%Y-%m-%d"), ts.strftime("%H:%M")


def _write_page(out_root: Path, feed: dict, day: str, entries: list[dict]) -> None:
    lines = []
    for e in sorted(entries, key=lambda x: (x["hm"], x["title"])):
        line = f"- **{e['hm']}** [{e['title']}]({e['link']})" if e["link"] else f"- **{e['hm']}** {e['title']}"
        if e["summary"] and e["summary"] != e["title"]:
            line += f" — {e['summary']}"
        lines.append(line)
    body = (
        f"---\ntitle: RSS {feed['name']} — {day}\nsource: rss\nfeed: {feed['slug']}\n"
        f"date: {day}\nurl: {feed['url']}\ntags: [rss, finance, news, {feed['slug']}]\n---\n\n"
        f"# {feed['name']} — {day}\n\n" + "\n".join(lines) + "\n"
    )
    p = out_root / "rss" / feed["slug"] / f"{day}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--max", type=int, default=50, help="max entries/feed to consider per run")
    args = ap.parse_args()

    out_root = Path(args.output_dir)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    seen: dict[str, list[str]] = state.get("seen", {})
    days: dict[str, dict[str, list[dict]]] = state.get("days", {})
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=DAYS_KEPT)).strftime("%Y-%m-%d")

    feeds = _feeds()
    total_new, pages, ok, failed = 0, 0, 0, []
    with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as client:
        for feed in feeds:
            try:
                r = client.get(feed["url"])
                r.raise_for_status()
                try:
                    items = _items_etree(r.text)
                except ET.ParseError:
                    items = _items_regex(r.text)
            except Exception as e:  # noqa: BLE001 — one dead feed never kills the run
                failed.append(f"{feed['slug']} ({type(e).__name__})")
                continue
            ok += 1
            feed_seen = set(seen.get(feed["slug"], []))
            new_ids: list[str] = []
            feed_days = days.setdefault(feed["slug"], {})
            dirty: set[str] = set()
            for it in items[: args.max]:
                sid = _entry_id(feed["url"], it)
                if sid in feed_seen:
                    continue
                feed_seen.add(sid)
                new_ids.append(sid)
                day, hm = _bucket_day(it.get("ts"), now)
                feed_days.setdefault(day, []).append({
                    "hm": hm,
                    "title": it.get("title", ""),
                    "link": it.get("link", ""),
                    "summary": (it.get("summary") or "")[:SUMMARY_LEN],
                })
                dirty.add(day)
                total_new += 1
            for day in sorted(dirty):
                _write_page(out_root, feed, day, feed_days[day])
                pages += 1
            # prune: drop day buckets past the window; cap the dedup-id list
            # (insertion order preserved → oldest ids dropped first)
            for day in [d for d in feed_days if d < cutoff]:
                del feed_days[day]
            seen[feed["slug"]] = (seen.get(feed["slug"], []) + new_ids)[-SEEN_CAP:]

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"seen": seen, "days": days}))
    print(f"feeds ok: {ok}/{len(feeds)} | new entries: {total_new} | pages written: {pages}"
          + (f" | failed: {', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
