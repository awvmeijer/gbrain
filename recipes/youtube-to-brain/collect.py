"""YouTube → GBrain collector (deterministic; FREE, no YouTube Data API).

For each creator in channels.txt: resolve the channel id from the public
channel page, read the channel RSS feed for the latest videos, fetch each
video's auto-caption transcript (youtube-transcript-api), and write one
markdown page per video. GBrain then embeds + synthesizes; a daily digest is
a `gbrain think` over the recent youtube pages (see README).

FREE end-to-end: channel page + RSS + transcript API need no key/quota.

State: per-channel seen-video-id set in <out>/youtube/.seen.json → re-runs only
fetch new videos. Idempotent (gbrain import dedups by content hash anyway).

Usage:
    .venv/bin/python collect.py <output_dir> [--limit 2] [--max-chars 40000]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import httpx

# --- content-staleness guard ------------------------------------------------
# YouTube's publish date is authoritative for *when the video entered YouTube*,
# but re-premiered / recycled livestreams carry a current publish date over
# year-old content (e.g. a June-2025 stream re-run on 2026-06-29). A pure
# publish-date collector can't see this. Cheap, deterministic tell: the
# transcript asserts weekday↔calendar-date pairings ("Thursday July 3") that
# are only valid a year earlier than the publish year. No LLM, no false
# drop — we only *flag* (never lose content), so window-synthesis can skip it.
_WD_RE = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_MO_RE = (
    r"(january|february|march|april|may|june|july|august|september|october"
    r"|november|december)"
)
_WD = {n: i for i, n in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
_MO = {n: i for i, n in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def suspect_content_year(text: str, published: str) -> int | None:
    """Advisory: return the year the content likely belongs to when the
    transcript/digest asserts weekday↔date pairings ("Thursday July 3") that
    fit publish_year-1, else None.

    Deliberately conservative to avoid tarring genuine current videos that
    carry a single loose holiday reference (e.g. a real 2026-06-30 upload
    saying "Friday July 4" when 2026's 4th is a Saturday): we flag ONLY when
    >=2 *distinct* dates all resolve to publish_year-1 AND none resolve to the
    publish year itself. Never overwrites the (authoritative) YouTube date and
    never drops the page — it's a hint for window-synthesis / human review."""
    try:
        pub_year = int(published[:4])
    except (ValueError, TypeError):
        return None
    if pub_year < 2000:
        return None
    t = text.lower()
    # distinct (month, day) sets that resolve to each candidate year — a given
    # (weekday, month, day) fits at most one year in a 3-year window.
    hits = {pub_year - 1: set(), pub_year: set(), pub_year + 1: set()}

    def tally(wd: str, mo: str, day: int) -> None:
        wdi, moi = _WD.get(wd), _MO.get(mo)
        if wdi is None or moi is None:
            return
        for y in hits:
            try:
                if date(y, moi, day).weekday() == wdi:
                    hits[y].add((moi, day))
            except ValueError:  # e.g. Feb 30
                pass

    # "Thursday (July 3)" / "Thursday, July 3rd"
    for m in re.finditer(rf"{_WD_RE}[^a-z0-9]{{0,15}}{_MO_RE}\.?\s+(\d{{1,2}})", t):
        tally(m.group(1), m.group(2), int(m.group(3)))
    # "July 3rd ... Thursday"
    for m in re.finditer(rf"{_MO_RE}\.?\s+(\d{{1,2}})[a-z]{{0,3}}[^a-z0-9]{{0,15}}{_WD_RE}", t):
        tally(m.group(3), m.group(1), int(m.group(2)))

    if len(hits[pub_year - 1]) >= 2 and not hits[pub_year]:
        return pub_year - 1
    return None

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}
HERE = Path(__file__).parent


def _channels() -> list[str]:
    out = []
    for line in (HERE / "channels.txt").read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s if s.startswith("@") else "@" + s)
    return out


def _resolve_channel_id(client: httpx.Client, handle: str) -> str | None:
    pats = (
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]+)"',
        r'"externalId":"(UC[\w-]+)"',
        r'"browseId":"(UC[\w-]+)"',
        r'channel/(UC[\w-]+)',
    )
    # Some handle pages don't expose the id on the root tab; /videos and /about
    # do (e.g. @ARKInvest2015). Try in order.
    for suffix in ("", "/videos", "/about"):
        try:
            r = client.get(f"https://www.youtube.com/{handle}{suffix}", headers=UA, follow_redirects=True, timeout=25)
        except Exception:  # noqa: BLE001
            continue
        for pat in pats:
            m = re.search(pat, r.text)
            if m:
                return m.group(1)
    return None


def _latest_videos(client: httpx.Client, channel_id: str) -> list[dict]:
    rss = client.get(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        headers=UA, timeout=25,
    ).text
    # entries are in document order (newest first)
    entries = re.findall(r"<entry>(.*?)</entry>", rss, re.DOTALL)
    out = []
    for e in entries:
        vid = re.search(r"<yt:videoId>([\w-]+)</yt:videoId>", e)
        title = re.search(r"<title>(.*?)</title>", e, re.DOTALL)
        pub = re.search(r"<published>(.*?)</published>", e)
        if vid:
            out.append({
                "id": vid.group(1),
                "title": (title.group(1) if title else "").strip(),
                "published": (pub.group(1)[:10] if pub else ""),
            })
    return out


def _transcript(video_id: str) -> str | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id)
        return " ".join(s.text for s in fetched).strip()
    except Exception as e:  # noqa: BLE001 — no captions / disabled / blocked
        print(f"    transcript miss {video_id}: {type(e).__name__}", file=sys.stderr)
        return None


def _slug(h: str) -> str:
    return h.lstrip("@").lower()


_SUMM_SYS = (
    "You summarize a finance YouTube video transcript into a tight digest. "
    "Output ONLY: a 1-2 sentence thesis; a bulleted list of tickers/calls with "
    "direction (bullish/bearish/watch); key price levels, catalysts, or dates; "
    "and any notable or contrarian claims. No preamble, no filler."
)


def _summarize(transcript: str, bridge: str) -> str | None:
    """Per-video digest via the Max bridge (Claude 200k ctx fits a transcript)."""
    try:
        r = httpx.post(
            f"{bridge}/v1/chat/completions",
            json={
                "model": "claude-sonnet",
                "messages": [
                    {"role": "system", "content": _SUMM_SYS},
                    {"role": "user", "content": transcript[:60000]},
                ],
            },
            timeout=150,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001 — digest is best-effort; keep the transcript page
        print(f"    summarize miss: {type(e).__name__}", file=sys.stderr)
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--limit", type=int, default=2, help="latest videos per channel")
    ap.add_argument("--max-chars", type=int, default=40000, help="cap transcript length")
    ap.add_argument("--summarize", action="store_true", help="per-video digest via the Max bridge")
    ap.add_argument("--bridge", default="http://127.0.0.1:8789", help="Max bridge base url")
    args = ap.parse_args()

    out_root = Path(args.output_dir)
    seen_path = out_root / "youtube" / ".seen.json"
    seen = {}
    if seen_path.exists():
        seen = json.loads(seen_path.read_text())

    written = 0
    # SOCS=CAI = a stored "reject all" consent choice. Without it, EU IPs get
    # 302'd to consent.youtube.com (started 2026-07-04) and the interstitial
    # carries no channel id → every handle resolved None, pages written: 0.
    with httpx.Client(cookies={"SOCS": "CAI"}) as client:
        for handle in _channels():
            cid = _resolve_channel_id(client, handle)
            if not cid:
                print(f"{handle}: channel id NOT resolved", file=sys.stderr)
                continue
            vids = _latest_videos(client, cid)[: args.limit]
            seen_ids = set(seen.get(handle, []))
            for v in vids:
                if v["id"] in seen_ids:
                    continue
                txt = _transcript(v["id"])
                if not txt:
                    continue
                if len(txt) > args.max_chars:
                    txt = txt[: args.max_chars] + "\n\n[transcript truncated]"
                title = v["title"] or v["id"]
                safe_title = title.replace('"', "'")
                digest = _summarize(txt, args.bridge) if args.summarize else None
                section = f"## Digest\n\n{digest}\n\n## Transcript\n\n" if digest else ""
                # Recycled/re-premiered stream check (scan digest+transcript,
                # where date anchors are densest). Advisory only.
                stale_year = suspect_content_year((digest or "") + "\n" + txt, v["published"])
                stale_fm = (
                    f"stale_content: true\ncontent_date_review: {stale_year}\n" if stale_year else ""
                )
                stale_note = (
                    f"> ⚠️ **Content-date review:** internal weekday/date references fit "
                    f"**{stale_year}**, not the {v['published'][:4]} publish date — likely a "
                    f"re-premiered/recycled stream. Treat calls as out-of-window.\n\n"
                    if stale_year else ""
                )
                body = (
                    f"---\n"
                    f"title: {safe_title}\n"
                    f"source: youtube\n"
                    f"channel: {handle}\n"
                    f"channel_id: {cid}\n"
                    f"video_id: {v['id']}\n"
                    f"url: https://youtu.be/{v['id']}\n"
                    f"date: {v['published']}\n"
                    f"has_digest: {'true' if digest else 'false'}\n"
                    f"{stale_fm}"
                    f"tags: [youtube, finance, {_slug(handle)}]\n"
                    f"---\n\n"
                    f"# {safe_title}\n\n"
                    f"_{handle} · {v['published']} · https://youtu.be/{v['id']}_\n\n"
                    f"{stale_note}{section}{txt}\n"
                )
                p = out_root / "youtube" / _slug(handle) / f"{v['id']}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
                seen_ids.add(v["id"])
                written += 1
                print(f"  {handle} → {v['id']} ({len(txt)} chars) {safe_title[:60]}")
            seen[handle] = sorted(seen_ids)

    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, indent=2))
    print(f"pages written: {written}")


if __name__ == "__main__":
    main()
