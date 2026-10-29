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
import os
import re
import subprocess
import sys
import tempfile
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


# --- unblocking YouTube -------------------------------------------------------
# 2026-09-13 onward both fetch paths went dark: the timedtext endpoint returns
# IpBlocked for this residential IP, and yt-dlp gets "Sign in to confirm you're
# not a bot". Seven days of zero pages. Both gates lift with a logged-in cookie
# jar, so both paths read the SAME Netscape-format file.
#
# A FILE, never --cookies-from-browser: that path decrypts via the login
# Keychain, and a Keychain read from a launchd subprocess blocks on securityd
# waiting for a prompt no cron can answer (standing rule, 2026-09-15). The file
# is 0600 under ~/.gbrain/ like every other secret here.
#
# Optional egress proxy for the transcript API (YOUTUBE_PROXY, e.g.
# http://user:pass@host:port). Left unset by default — no metered spend.
# Everything here degrades to exactly the old behaviour when unconfigured.
COOKIE_FILE = Path(os.environ.get(
    "YOUTUBE_COOKIES", str(Path.home() / ".gbrain" / "youtube-cookies.txt")))
YOUTUBE_PROXY = os.environ.get("YOUTUBE_PROXY", "").strip()


# Only these domains belong in the jar. `--cookies-from-browser chrome` dumps
# the ENTIRE Chrome cookie store — 2262 cookies across 675 domains on this
# machine, every logged-in site plus a few hundred ad trackers — into one
# plaintext file. YouTube needs 8 domains. Every refresh trims back down, so an
# automated re-export can never quietly recreate that credential pile.
_GOOGLE_DOMAIN = re.compile(
    r"(^|\.)(youtube\.com|google\.com|googlevideo\.com|ytimg\.com|ggpht\.com|"
    r"gstatic\.com|googleapis\.com|youtube-nocookie\.com)$")

# Which browser to re-export from, and how long to let that attempt run.
COOKIE_BROWSER = os.environ.get("YOUTUBE_COOKIE_BROWSER", "chrome")
COOKIE_REFRESH_TIMEOUT = int(os.environ.get("YOUTUBE_COOKIE_REFRESH_TIMEOUT", "45"))
COOKIE_REFRESH_AFTER = 3          # block-type misses before trying a refresh
_COOKIE_PROBE = "dQw4w9WgXcQ"     # any always-available video
# Present only in a signed-in jar; any one of them means the export is usable.
_LOGIN_COOKIES = frozenset({"SID", "__Secure-1PSID", "__Secure-3PSID"})


def _trim_cookies(text: str) -> tuple[str, int]:
    """Keep only Google/YouTube cookie lines. Returns (netscape_text, kept)."""
    out = ["# Netscape HTTP Cookie File"]
    kept = 0
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        if _GOOGLE_DOMAIN.search(parts[0].lstrip(".")):
            out.append(line)
            kept += 1
    return "\n".join(out) + "\n", kept


def _install_cookie_file(text: str) -> None:
    """Atomically replace the jar at 0600 — it is a live Google session."""
    tmp = COOKIE_FILE.with_name(COOKIE_FILE.name + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, COOKIE_FILE)


def _refresh_cookies_from_browser() -> bool:
    """Re-export cookies from the local browser, trimmed to Google domains.

    Hard-timed on purpose. Decrypting Chrome's cookie store needs the login
    Keychain, and a Keychain read from a launchd subprocess blocks on securityd
    waiting for a prompt no cron can answer (standing rule, 2026-09-15). The
    timeout means the nightly run degrades to "still stale — banner fires"
    instead of wedging the pipeline. Run interactively, it just works.

    Success is judged by the FILE, never the exit code: yt-dlp writes the jar
    and *then* often fails the metadata fetch ("The page needs to be reloaded"),
    exiting non-zero with perfectly good cookies already on disk.
    """
    ytdlp = str(HERE / ".venv" / "bin" / "yt-dlp")
    with tempfile.TemporaryDirectory(prefix="yt-cookies-") as td:
        dest = Path(td) / "cookies.txt"
        try:
            subprocess.run(
                [ytdlp, "--cookies-from-browser", COOKIE_BROWSER,
                 "--cookies", str(dest), "--skip-download", "--quiet",
                 "--no-warnings", f"https://www.youtube.com/watch?v={_COOKIE_PROBE}"],
                capture_output=True, text=True, timeout=COOKIE_REFRESH_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"    cookies: refresh from {COOKIE_BROWSER} timed out after "
                  f"{COOKIE_REFRESH_TIMEOUT}s (Keychain locked, or no GUI session) "
                  f"— jar left as-is", file=sys.stderr)
            return False
        except OSError as e:
            print(f"    cookies: refresh failed ({type(e).__name__})", file=sys.stderr)
            return False
        if not dest.is_file():
            print("    cookies: browser export produced no file", file=sys.stderr)
            return False
        text, kept = _trim_cookies(dest.read_text(errors="ignore"))
        # Exact cookie names (column 6), not substrings: "SID" is inside SIDCC,
        # which Google also sets for signed-out visitors.
        names = {ln.split("\t")[5] for ln in text.splitlines()[1:]}
        if not names & _LOGIN_COOKIES:
            print("    cookies: export carried no login cookies — not signed in?",
                  file=sys.stderr)
            return False
        _install_cookie_file(text)
        print(f"    cookies: refreshed from {COOKIE_BROWSER} "
              f"({kept} google cookies, 0600)", file=sys.stderr)
        return True


def _cookie_file() -> Path | None:
    """The cookie jar, if it exists and is non-empty."""
    try:
        if COOKIE_FILE.is_file() and COOKIE_FILE.stat().st_size > 0:
            return COOKIE_FILE
    except OSError:
        pass
    return None


_CLIENT: list = []   # memoized session (or [None]) — one jar for the whole run
_JAR: list = []      # the live MozillaCookieJar, so rotations can be saved back


def _transcript_client():
    """A requests.Session carrying the cookie jar, or None to use the default.

    youtube_transcript_api takes `http_client`; loading cookies into a session
    is what turns an IpBlocked anonymous fetch into an authenticated one.
    Memoized so one jar serves the whole run and accumulates the rotations
    Google hands back — see _save_cookies for why that matters.
    """
    if _CLIENT:
        return _CLIENT[0]
    cf = _cookie_file()
    if cf is None:
        _CLIENT.append(None)
        return None
    try:
        import http.cookiejar
        import requests
        jar = http.cookiejar.MozillaCookieJar(str(cf))
        jar.load(ignore_discard=True, ignore_expires=True)
        sess = requests.Session()
        sess.cookies = jar  # type: ignore[assignment]
        sess.headers.update({"User-Agent": UA_STR, "Accept-Language": "en-US,en;q=0.9"})
        _JAR.append(jar)
        _CLIENT.append(sess)
        return sess
    except Exception as e:  # noqa: BLE001 — malformed jar must not kill the run
        print(f"    cookies: unusable ({type(e).__name__}) — anonymous fetch",
              file=sys.stderr)
        _CLIENT.append(None)
        return None


def _save_cookies() -> None:
    """Write the jar back, keeping the session alive across runs.

    THIS is what stops the cookies going stale. Google re-issues the rotating
    auth cookies (__Secure-1PSIDTS, SIDCC) on essentially every request; a jar
    that is read but never written keeps re-presenting the snapshot taken at
    export time until the server stops accepting it. Persisting each rotation
    means the session renews itself nightly and only a real invalidation —
    password change, explicit sign-out, a Google security event — can end it.
    Nominal expiry is not the constraint (SIDCC is dated a year out); the stale
    rotating token is.
    """
    if not _JAR:
        return
    try:
        tmp = COOKIE_FILE.with_name(COOKIE_FILE.name + ".tmp")
        _JAR[0].save(str(tmp), ignore_discard=True, ignore_expires=True)
        text, kept = _trim_cookies(tmp.read_text(errors="ignore"))
        tmp.unlink(missing_ok=True)
        if kept:
            _install_cookie_file(text)
    except Exception as e:  # noqa: BLE001 — never fail a run over bookkeeping
        print(f"    cookies: could not persist rotations ({type(e).__name__})",
              file=sys.stderr)


UA_STR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/127.0 Safari/537.36")

_WARNED: list[str] = []
_BLOCK_HITS: list[str] = []   # block-type transcript misses this run
_REFRESHED: list[bool] = []   # at most one browser re-export per run


def _warn_once(key: str, msg: str) -> None:
    if key not in _WARNED:
        _WARNED.append(key)
        print(msg, file=sys.stderr)


def _transcript(video_id: str) -> str | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    kwargs = {}
    client = _transcript_client()
    if client is not None:
        kwargs["http_client"] = client
    elif not YOUTUBE_PROXY:
        _warn_once("nocookies",
                   f"    note: no cookie jar at {COOKIE_FILE} and no YOUTUBE_PROXY "
                   f"— anonymous fetch, expect IpBlocked")
    if YOUTUBE_PROXY:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig
            kwargs["proxy_config"] = GenericProxyConfig(
                http_url=YOUTUBE_PROXY, https_url=YOUTUBE_PROXY)
        except Exception as e:  # noqa: BLE001
            _warn_once("noproxy", f"    proxy config unusable: {type(e).__name__}")
    try:
        fetched = YouTubeTranscriptApi(**kwargs).fetch(video_id)
        return " ".join(s.text for s in fetched).strip()
    except Exception as e:  # noqa: BLE001 — no captions / disabled / blocked
        name = type(e).__name__
        print(f"    transcript miss {video_id}: {name}", file=sys.stderr)
        # A run of block-type misses means the jar went stale (or never
        # existed). Try ONE browser re-export per run, then retry this video
        # once. Captionless videos raise a different error and never get here,
        # so this cannot fire on ordinary misses.
        if ("Block" in name or "TooManyRequests" in name) and not _REFRESHED:
            _BLOCK_HITS.append(video_id)
            if len(_BLOCK_HITS) >= COOKIE_REFRESH_AFTER:
                _REFRESHED.append(True)
                print(f"    cookies: {len(_BLOCK_HITS)} block-type misses — "
                      f"re-exporting from {COOKIE_BROWSER}", file=sys.stderr)
                if _refresh_cookies_from_browser():
                    _CLIENT.clear()
                    _JAR.clear()
                    return _transcript(video_id)
        return None


def _slug(h: str) -> str:
    return h.lstrip("@").lower()


def _yaml_str(s: str) -> str:
    """A frontmatter-safe scalar: control chars stripped, then JSON-quoted
    (valid YAML double-quoted flow scalar). Unquoted values broke the gbrain
    import for every page here — `@handle` starts with a YAML reserved
    indicator, and titles carry `: `, quotes, and (via a backfill decode bug)
    C1 control bytes."""
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", s)
    return json.dumps(s, ensure_ascii=False)


# --- ASR fallback (captionless videos) ---------------------------------------
# Previous behavior: no auto-captions → video silently DROPPED. Fallback: pull
# the audio with yt-dlp (already in this venv for backfill.py) and transcribe
# locally with MLX Whisper — same model + invocation as the capture sidecar
# (sidecars/capture/server.py). This venv doesn't carry mlx_whisper; the
# transcription shells out to the capture sidecar's venv python (override with
# BRAINS_ASR_PYTHON). If that interpreter can't import mlx_whisper the fallback
# disables itself and captionless videos are skipped exactly as before. Audio
# never leaves the machine. Budget: ASR_MAX_PER_RUN videos per run (protect
# the 06:30 cron window; the nightly backfill drip absorbs the rest) and
# ASR_MAX_ATTEMPTS strikes per video — tracked in <out>/youtube/.asr.json,
# pre-marked before each try so even a watchdog-killed run counts one — then
# the video is marked seen. Nothing is retried forever.
ASR_PYTHON = os.environ.get(
    "BRAINS_ASR_PYTHON",
    str(Path.home() / "gbrain" / "sidecars" / ".venv" / "bin" / "python"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-small-mlx")
ASR_MAX_PER_RUN = 2
ASR_MAX_ATTEMPTS = 2
ASR_MAX_DURATION = 7200  # skip >2h streams — transcribe time would blow the window
_ASR_OK: list[bool] = []  # memoized availability probe


def _asr_available() -> bool:
    if not _ASR_OK:
        try:
            ok = subprocess.run([ASR_PYTHON, "-c", "import mlx_whisper"],
                                capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            print(f"    asr: mlx_whisper not importable via {ASR_PYTHON} — "
                  "fallback disabled", file=sys.stderr)
        _ASR_OK.append(ok)
    return _ASR_OK[0]


def _save_asr(path: Path, state: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _asr_transcript(video_id: str) -> str | None:
    """Audio via yt-dlp → local MLX Whisper. Every subprocess is timed so a
    hung download or decode can never wedge the cron (feeds-cron's per-step
    watchdog is the backstop above this)."""
    ytdlp = str(HERE / ".venv" / "bin" / "yt-dlp")
    with tempfile.TemporaryDirectory(prefix="yt-asr-") as td:
        try:
            cmd = [ytdlp, "-f", "bestaudio[ext=m4a]/bestaudio",
                   "-o", str(Path(td) / "audio.%(ext)s"),
                   "--match-filter", f"duration<={ASR_MAX_DURATION}",
                   "--socket-timeout", "20", "--quiet", "--no-warnings"]
            cf = _cookie_file()
            if cf is not None:  # lifts "Sign in to confirm you're not a bot"
                cmd += ["--cookies", str(cf)]
            cmd.append(f"https://youtu.be/{video_id}")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"    asr: download failed {video_id}: {type(e).__name__}",
                  file=sys.stderr)
            return None
        audio = next(iter(sorted(Path(td).glob("audio.*"))), None)
        if audio is None:
            tail = (r.stderr or "").strip().splitlines()
            print(f"    asr: no audio for {video_id} "
                  f"({tail[-1][:80] if tail else 'duration-filtered or blocked'})",
                  file=sys.stderr)
            return None
        try:
            w = subprocess.run(
                [ASR_PYTHON, "-c",
                 "import sys, mlx_whisper\n"
                 "out = mlx_whisper.transcribe(sys.argv[1], path_or_hf_repo=sys.argv[2])\n"
                 "print((out.get('text') or '').strip())",
                 str(audio), WHISPER_MODEL],
                capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"    asr: whisper failed {video_id}: {type(e).__name__}",
                  file=sys.stderr)
            return None
        if w.returncode != 0:
            print(f"    asr: whisper failed {video_id}: {w.stderr.strip()[:80]}",
                  file=sys.stderr)
            return None
        txt = w.stdout.strip()
        if txt:
            print(f"    asr: transcribed {video_id} locally ({len(txt)} chars)",
                  file=sys.stderr)
        return txt or None


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
    asr_path = out_root / "youtube" / ".asr.json"
    asr_state: dict[str, int] = (
        json.loads(asr_path.read_text()) if asr_path.exists() else {})
    asr_used = 0

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
                asr = False
                if not txt:
                    # No auto-captions → local Whisper fallback (see the ASR
                    # block above). Attempts are pre-marked in .asr.json so a
                    # watchdog-killed run still counts one.
                    tries = asr_state.get(v["id"], 0)
                    if tries >= ASR_MAX_ATTEMPTS:
                        print(f"    asr: gave up on {v['id']} after {tries} attempts",
                              file=sys.stderr)
                        seen_ids.add(v["id"])
                    elif asr_used < ASR_MAX_PER_RUN and _asr_available():
                        asr_used += 1
                        asr_state[v["id"]] = tries + 1
                        _save_asr(asr_path, asr_state)
                        txt = _asr_transcript(v["id"])
                        if txt:
                            asr = True
                            asr_state.pop(v["id"], None)
                            _save_asr(asr_path, asr_state)
                if not txt:
                    continue
                if len(txt) > args.max_chars:
                    txt = txt[: args.max_chars] + "\n\n[transcript truncated]"
                title = v["title"] or v["id"]
                safe_title = title.replace('"', "'")
                fm_title = _yaml_str(title)
                digest = _summarize(txt, args.bridge) if args.summarize else None
                section = f"## Digest\n\n{digest}\n\n## Transcript\n\n" if digest else ""
                # Recycled/re-premiered stream check (scan digest+transcript,
                # where date anchors are densest). Advisory only.
                stale_year = suspect_content_year((digest or "") + "\n" + txt, v["published"])
                stale_fm = (
                    f"stale_content: true\ncontent_date_review: {stale_year}\n" if stale_year else ""
                )
                asr_fm = "asr: whisper-local\n" if asr else ""
                stale_note = (
                    f"> ⚠️ **Content-date review:** internal weekday/date references fit "
                    f"**{stale_year}**, not the {v['published'][:4]} publish date — likely a "
                    f"re-premiered/recycled stream. Treat calls as out-of-window.\n\n"
                    if stale_year else ""
                )
                body = (
                    f"---\n"
                    f"title: {fm_title}\n"
                    f"source: youtube\n"
                    f"channel: {_yaml_str(handle)}\n"
                    f"channel_id: {cid}\n"
                    f"video_id: {v['id']}\n"
                    f"url: https://youtu.be/{v['id']}\n"
                    f"date: {v['published']}\n"
                    f"has_digest: {'true' if digest else 'false'}\n"
                    f"{asr_fm}"
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
    _save_cookies()
    print(f"pages written: {written}")


if __name__ == "__main__":
    main()
