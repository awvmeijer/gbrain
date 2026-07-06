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
from pathlib import Path

import httpx

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
                    f"tags: [youtube, finance, {_slug(handle)}]\n"
                    f"---\n\n"
                    f"# {safe_title}\n\n"
                    f"_{handle} · {v['published']} · https://youtu.be/{v['id']}_\n\n"
                    f"{section}{txt}\n"
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
