#!/usr/bin/env python3
"""YouTube history backfill — one-shot deep ingest per channel.

The nightly collect.py rides the channel RSS feed, which only exposes the ~15
most recent uploads — fine for monitoring, useless for history. This script
enumerates a channel's full upload list via yt-dlp's flat playlist (1 request
per channel), then for each video not yet in .seen.json fetches the watch page
(true upload date via "uploadDate") and the auto-caption transcript.

Pages match collect.py's format exactly and share .seen.json, so the nightly
cron and this backfill never duplicate work. No --summarize here: transcripts
alone are searchable/embeddable; per-video digests stay a nightly-cron,
new-video-only cost. Backfilled pages carry `backfill: true`.

Deliberately gentle: ~1s sleep between videos — youtube-transcript-api from a
single IP gets rate-limited if hammered; a few hundred videos is a lunch-break
job, not a sprint.

Usage:
    .venv/bin/python backfill.py <output_dir> [--per-channel 25] [--max-chars 40000]
    .venv/bin/python backfill.py <output_dir> --channels @HumbledTraderOfficial --per-channel 100
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

from collect import UA, _channels, _slug, _transcript, suspect_content_year

HERE = Path(__file__).parent
YTDLP = str(HERE / ".venv" / "bin" / "yt-dlp")


def enumerate_uploads(handle: str, limit: int) -> list[str]:
    """Full upload list (newest first) via flat playlist — one request."""
    try:
        out = subprocess.run(
            [YTDLP, "--flat-playlist", "--print", "%(id)s",
             "--playlist-end", str(limit),
             f"https://www.youtube.com/{handle}/videos"],
            capture_output=True, text=True, timeout=120,
        )
        ids = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if out.returncode != 0 and not ids:
            print(f"  {handle}: yt-dlp failed: {out.stderr.strip()[:200]}", file=sys.stderr)
        return ids
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  {handle}: enumerate failed: {type(e).__name__}", file=sys.stderr)
        return []


def watch_meta(client: httpx.Client, video_id: str) -> dict:
    """Title + true upload date + channel id from the watch page."""
    meta = {"title": video_id, "date": "", "cid": ""}
    try:
        r = client.get(f"https://www.youtube.com/watch?v={video_id}",
                       headers=UA, follow_redirects=True, timeout=25)
        if m := re.search(r'"uploadDate":"(\d{4}-\d{2}-\d{2})', r.text):
            meta["date"] = m.group(1)
        if m := re.search(r'"videoDetails":.*?"title":"((?:[^"\\]|\\.)*)"', r.text):
            meta["title"] = m.group(1).encode().decode("unicode_escape", "ignore")
        if m := re.search(r'"channelId":"(UC[\w-]+)"', r.text):
            meta["cid"] = m.group(1)
    except Exception as e:  # noqa: BLE001
        print(f"    meta miss {video_id}: {type(e).__name__}", file=sys.stderr)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--per-channel", type=int, default=25)
    ap.add_argument("--max-chars", type=int, default=40000)
    ap.add_argument("--channels", nargs="*", help="subset of @handles (default: channels.txt)")
    args = ap.parse_args()

    out_root = Path(args.output_dir).expanduser()
    seen_path = out_root / "youtube" / ".seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}

    handles = [h if h.startswith("@") else "@" + h for h in (args.channels or _channels())]
    written = skipped = missed = 0
    with httpx.Client(cookies={"SOCS": "CAI"}) as client:  # consent-wall bypass
        for handle in handles:
            ids = enumerate_uploads(handle, args.per_channel)
            seen_ids = set(seen.get(handle, []))
            todo = [v for v in ids if v not in seen_ids]
            print(f"{handle}: {len(ids)} enumerated, {len(todo)} new")
            for vid in todo:
                txt = _transcript(vid)
                if not txt:
                    missed += 1
                    seen_ids.add(vid)  # don't retry caption-less videos nightly
                    continue
                if len(txt) > args.max_chars:
                    txt = txt[: args.max_chars] + "\n\n[transcript truncated]"
                meta = watch_meta(client, vid)
                safe_title = meta["title"].replace('"', "'")
                stale_year = suspect_content_year(txt, meta["date"])
                stale_fm = (
                    f"stale_content: true\ncontent_date_review: {stale_year}\n"
                    if stale_year else ""
                )
                body = (
                    f"---\n"
                    f"title: {safe_title}\n"
                    f"source: youtube\n"
                    f"channel: {handle}\n"
                    f"channel_id: {meta['cid']}\n"
                    f"video_id: {vid}\n"
                    f"url: https://youtu.be/{vid}\n"
                    f"date: {meta['date']}\n"
                    f"has_digest: false\n"
                    f"backfill: true\n"
                    f"{stale_fm}"
                    f"tags: [youtube, finance, {_slug(handle)}]\n"
                    f"---\n\n"
                    f"# {safe_title}\n\n"
                    f"_{handle} · {meta['date']} · https://youtu.be/{vid}_\n\n"
                    f"{txt}\n"
                )
                p = out_root / "youtube" / _slug(handle) / f"{vid}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
                seen_ids.add(vid)
                written += 1
                print(f"  {handle} → {vid} ({meta['date']}) {safe_title[:55]}")
                time.sleep(1.0)  # be a polite scraper
            skipped += len(ids) - len(todo)
            seen[handle] = sorted(seen_ids)
            # persist per channel so an interrupt loses nothing
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(seen, indent=2))

    print(f"backfill: {written} written, {skipped} already seen, {missed} no-transcript")


if __name__ == "__main__":
    main()
