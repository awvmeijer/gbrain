#!/usr/bin/env python3
"""Feeds watchdog: detect a silent feed and run its known remediation.

Reads per-source freshness (capture sidecar /api/health, falling back to dating
pages on disk when the sidecar is down — the watchdog must not depend on the
thing it watches) plus the X collector's run state, decides which daily feeds
are unhealthy, runs each one's remediation once, re-imports + embeds only if
something new landed, and writes a one-line-per-source summary to
logs/selfheal/<YYYY-MM-DD>.txt (digest.py reads it) and logs/selfheal.log.

Never posts anywhere. Never kickstarts com.brains.feeds.

Usage:
    feeds-watchdog.py [--dry-run]     # dry-run: diagnose + print plan, change nothing
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

HOME = Path.home()
GB = HOME / "gbrain"
ING = HOME / "brains-ingest"
LOGS = GB / "logs"
OUT_DIR = LOGS / "selfheal"
BV = GB / "sidecars/.venv/bin/python"
YV = GB / "recipes/youtube-to-brain/.venv/bin/python"
X_STATE = LOGS / "x-collect-state.json"
STEP_TIMEOUT = 1200
X_MIN_RATIO = 0.5
X_MAX_AGE_H = 30

# Daily feeds and their remediation: one retry of the same collector the
# 06:30 cron runs (X adds --heal: instance re-probe + retry + backfill).
REMEDIATE = {
    "x": [str(BV), str(GB / "recipes/x-to-brain/collect.py"), str(ING),
          "--per-handle-sleep", "1.2", "--max", "25", "--heal"],
    "youtube": [str(YV), str(GB / "recipes/youtube-to-brain/collect.py"), str(ING),
                "--limit", "1", "--summarize"],
    "telegram": [str(BV), str(GB / "recipes/telegram-to-brain/collect.py"), str(ING), "--limit", "50"],
    "rss": [str(BV), str(GB / "recipes/rss-to-brain/collect.py"), str(ING), "--max", "50"],
}
# Mirrors DARK_AFTER_DAYS (digest.py) in hours, used only when the sidecar is down.
DISK_MAX_AGE_H = {"x": 30, "youtube": 72, "telegram": 30, "rss": 30}


def log(msg: str) -> None:
    print(f"[{datetime.now():%F %T}] watchdog: {msg}", flush=True)


def health_feeds() -> dict[str, dict] | None:
    try:
        key = (HOME / ".gbrain/client.key").read_text().strip()
        req = urllib.request.Request("http://127.0.0.1:8787/api/health?fast=1",
                                     headers={"x-brain-key": key})
        d = json.loads(urllib.request.urlopen(req, timeout=8).read())
        return {f["source"]: f for f in d.get("feeds", [])}
    except Exception:  # noqa: BLE001
        return None


def disk_age_h(src: str) -> float | None:
    """Hours since the newest page mtime under brains-ingest/<src>/."""
    root = ING / src
    if not root.is_dir():
        return None
    newest = max((p.stat().st_mtime for p in root.rglob("*.md")), default=None)
    return None if newest is None else (datetime.now().timestamp() - newest) / 3600


def diagnose() -> dict[str, str]:
    """source -> reason it is unhealthy (only unhealthy sources returned)."""
    bad: dict[str, str] = {}
    feeds = health_feeds()
    for src in REMEDIATE:
        if feeds is not None and src in feeds:
            f = feeds[src]
            if f.get("stale"):
                bad[src] = f"silent {f.get('age_hours', 0):.0f}h (health)"
        else:
            age = disk_age_h(src)
            if age is None or age > DISK_MAX_AGE_H[src]:
                bad[src] = ("no pages" if age is None else f"silent {age:.0f}h") + " (disk; sidecar down)"
    # X can be "fresh" from 2 surviving handles while 51 are dark: check coverage.
    try:
        st = json.loads(X_STATE.read_text())
        ok, tot = st.get("handles_ok", 0), st.get("handles_total", 0) or 1
        run_age = (datetime.now() - datetime.fromisoformat(st["last_run"])).total_seconds() / 3600
        if ok / tot < X_MIN_RATIO and "x" not in bad:
            bad["x"] = f"low coverage {ok}/{tot} handles at last run"
        elif run_age > X_MAX_AGE_H and "x" not in bad:
            bad["x"] = f"collector last ran {run_age:.0f}h ago"
    except (OSError, ValueError, KeyError):
        pass
    return bad


def run(cmd: list[str]) -> tuple[int, str]:
    env = dict(os.environ, PATH=f"{HOME}/.bun/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=STEP_TIMEOUT, env=env)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {STEP_TIMEOUT}s"
    except OSError as e:
        return 127, str(e)


def content_snapshot(src: str) -> dict[str, int]:
    """path -> hash of every page under brains-ingest/<src>/. Collectors rewrite
    unchanged pages (telegram rewrites ~50 a day), so "pages written" and mtimes
    both overstate; only a content diff says something new actually arrived."""
    root = ING / src
    if not root.is_dir():
        return {}
    return {str(p): hash(p.read_bytes()) for p in root.rglob("*.md")}


def summarize_x(out: str) -> str:
    m = re.search(r"handles ok: (\d+)/(\d+)", out)
    inst = re.search(r"^x-instances: .*$", out, re.M)
    try:
        bf = json.loads(X_STATE.read_text()).get("backfilled_days", [])
    except (OSError, ValueError):
        bf = []
    parts = [inst.group(0).removeprefix("x-instances: ") if inst else "",
             f"{m.group(1)}/{m.group(2)} handles" if m else "",
             f"backfilled {len(bf)} day(s)" if bf else ""]
    return ", ".join(p for p in parts if p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)
    lock = open(LOGS / "feeds-watchdog.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another watchdog run holds the lock; exiting")
        return 0

    bad = diagnose()
    if not bad:
        log("all daily feeds healthy")
        return 0
    for src, why in bad.items():
        log(f"{src}: {why} -> remediation: {' '.join(Path(c).name for c in REMEDIATE[src][:2])} ...")
    if args.dry_run:
        log("dry-run: no remediation run, nothing written")
        return 0

    lines, new_pages = [], 0
    for src, why in bad.items():
        before = content_snapshot(src)
        code, out = run(REMEDIATE[src])
        after = content_snapshot(src)
        n = sum(1 for k, v in after.items() if before.get(k) != v)
        new_pages += n
        detail = (summarize_x(out) + f", {n} new/changed page(s)") if src == "x" else f"{n} new/changed page(s)"
        if code != 0:
            status = f"REMEDIATION FAILED (exit {code})"
        elif n > 0:
            status = "repaired"
        else:
            # Collector ran clean but nothing new: upstream quiet, or still broken.
            status = "STILL SILENT after retry (collector ran clean — upstream quiet or blocked)"
        lines.append(f"{src}: {why} -> {status}" + (f", {detail}" if detail else ""))
        log(lines[-1])
    if new_pages:
        for cmd in (["gbrain", "import", str(ING), "--no-embed"], ["gbrain", "embed", "--stale"]):
            code, out = run(cmd)
            tail = (out.strip().splitlines() or [""])[-1][:160]
            log(f"{cmd[1]}: exit {code} {tail}")
            if code:
                lines.append(f"gbrain {cmd[1]} FAILED (exit {code})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M")
    with (OUT_DIR / f"{date.today().isoformat()}.txt").open("a") as f:
        for ln in lines:
            f.write(f"{stamp} {ln}\n")
    with (LOGS / "selfheal.log").open("a") as f:
        for ln in lines:
            f.write(f"[{datetime.now():%F %T}] {ln}\n")
    return 1 if any(("FAILED" in ln) or ("STILL SILENT" in ln) for ln in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
