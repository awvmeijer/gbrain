#!/usr/bin/env python3
"""Self-healing Nitter instance list for the X collector.

Why this exists: public Nitter instances are volunteer-run and die without
notice. 2026-08-21 the three hardcoded ones died and X was dark 30 nights;
2026-10-12 both entries in instances.txt went bad (403 / HTML instead of RSS)
and X was dark 3 nights until a human read the digest banner. Editing
instances.txt by hand is the step that keeps getting skipped, so this does it.

What it does (one call to heal()):
  1. Builds a candidate pool: the active lines of instances.txt, the healthy
     RSS-capable hosts reported by status.d420.de, and FALLBACK below.
  2. Probes each with a real RSS fetch of known handles and validates the body
     as RSS with at least one <item> (a 200 HTML anti-bot page or a 403 is dead).
  3. Rewrites instances.txt as working -> untested -> dead (dead capped, kept
     as last-resort failover because some instances are intermittent).
  4. Appends a dated record of every change to logs/x-instances.log and keeps
     per-instance history in logs/x-instances-state.json.

Hosts listed on a `# never: <host>` line in instances.txt are never re-added.

Usage:
    heal_instances.py [--dry-run] [--file PATH]   # probe, rewrite, print summary
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).parent
LOGS = HERE.parent.parent / "logs"
INSTANCES_FILE = HERE / "instances.txt"
LOG_FILE = LOGS / "x-instances.log"
STATE_FILE = LOGS / "x-instances-state.json"
STATUS_URL = "https://status.d420.de/api/v1/instances"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# Used when status.d420.de itself is unreachable. Hosts that served real RSS at
# some point; any of them may be dead now — the probe decides, not this list.
FALLBACK = [
    "https://nitter.meowing.monster",
    "https://nitter.kareem.one",
    "https://shitter.thepixora.com",
    "https://nitter.jaydenha.uk",
    "https://nitter.netbub.com",
    "https://nitter.miningtcup.me",
    "https://nitter.pp.ua",
    "https://nitter.privacyredirect.com",
    "https://nitter.tiekoetter.com",
    "https://xcancel.com",
]
MAX_PROBES = 16       # bound the probe wall-clock
MAX_DEAD_KEPT = 3     # dead instances kept active at the bottom as failover
PROBE_TIMEOUT = 12

HEADER = """\
# Nitter instances tried in order until one returns a parseable RSS feed.
# AUTO-MANAGED by heal_instances.py (runs before every X collect): it probes
# every instance here plus status.d420.de plus a fallback pool, and rewrites
# this file as working -> untested -> dead. Every change is logged to
# ~/gbrain/logs/x-instances.log. You can still edit by hand: add a host anywhere
# and the next probe sorts it; block one for good with a `# never: <host>` line.
"""


def _norm(s: str) -> str:
    s = s.split("#", 1)[0].strip().rstrip("/")
    if not s:
        return ""
    return s if "://" in s else f"https://{s}"


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url).rstrip("/")


def read_file(path: Path) -> tuple[list[str], set[str]]:
    """Active instance URLs in file order, and the `# never:` blocklist."""
    active, never = [], set()
    if not path.is_file():
        return active, never
    for line in path.read_text().splitlines():
        m = re.match(r"\s*#\s*never:\s*(\S+)", line)
        if m:
            never.add(_host(_norm(m.group(1))))
            continue
        u = _norm(line)
        if u and u not in active:
            active.append(u)
    return active, never


def status_pool(client: httpx.Client) -> list[str]:
    """Healthy, RSS-capable hosts from status.d420.de, best first. [] on failure."""
    try:
        r = client.get(STATUS_URL, headers=UA, timeout=10)
        r.raise_for_status()
        hosts = r.json().get("hosts", [])
    except Exception:  # noqa: BLE001 — the status page is itself volunteer-run
        return []
    hosts = [h for h in hosts if h.get("healthy") and h.get("rss") and h.get("url")]
    hosts.sort(key=lambda h: -(h.get("points") or 0))
    return [h["url"].rstrip("/") for h in hosts]


def is_rss(status: int, ctype: str, body: str) -> tuple[bool, str]:
    """Valid = HTTP 200, an RSS document (not an HTML page), >=1 <item>."""
    if status != 200:
        return False, f"HTTP {status}"
    head = body[:500].lower()
    if "<rss" not in head and "<?xml" not in head:
        return False, f"not RSS ({ctype.split(';')[0] or 'no content-type'})"
    if "<item>" not in body:
        return False, "RSS with 0 items"
    return True, f"{body.count('<item>')} items"


def probe(client: httpx.Client, url: str, handles: list[str]) -> tuple[bool, str]:
    """OK if ANY probe handle returns valid RSS (one quiet account is not death)."""
    reason = "no probe handles"
    for h in handles:
        try:
            r = client.get(f"{url}/{h}/rss", headers=UA, timeout=PROBE_TIMEOUT,
                           follow_redirects=True)
            ok, reason = is_rss(r.status_code, r.headers.get("content-type", ""), r.text)
        except Exception as e:  # noqa: BLE001
            ok, reason = False, type(e).__name__
        if ok:
            return True, f"{reason} via @{h}"
    return False, reason


def probe_handles(n: int = 2) -> list[str]:
    out = []
    f = HERE / "handles.txt"
    if f.is_file():
        for line in f.read_text().splitlines():
            s = line.strip().lstrip("@")
            if s and not s.startswith("#"):
                out.append(s)
    return out[:n] or ["beth_kindig"]


def _load_state(f: Path) -> dict:
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


def render(working: list[str], untested: list[str], dead: list[tuple[str, str]],
           never: set[str], stamp: str) -> str:
    out = [HEADER]
    out.append(f"# working (probed {stamp})")
    out += [_host(u) for u in working] or ["# (none — X is dark until one comes back)"]
    if untested:
        out += ["", "# untested (beyond the probe budget; tried after working)"]
        out += [_host(u) for u in untested]
    if dead:
        out += ["", f"# dead at {stamp} — kept as last-resort failover (some recover)"]
        out += [f"{_host(u)}  # {why}" for u, why in dead]
    if never:
        out += ["", "# blocklist — never re-added by the healer"]
        out += [f"# never: {h}" for h in sorted(never)]
    return "\n".join(out) + "\n"


def heal(path: Path = INSTANCES_FILE, dry_run: bool = False,
         use_status: bool = True, extra: list[str] | None = None) -> dict:
    """Probe, reorder and rewrite the instance list. Returns a summary dict."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    live = path.resolve() == INSTANCES_FILE.resolve()
    log_file = LOG_FILE if live else path.with_suffix(".log")
    state_file = STATE_FILE if live else path.with_suffix(".state.json")
    before, never = read_file(path)
    handles = probe_handles()
    with httpx.Client() as client:
        pool = before + (status_pool(client) if use_status else []) + FALLBACK + (extra or [])
        seen, cands = set(), []
        for u in pool:
            h = _host(u)
            if h and h not in seen and h not in never:
                seen.add(h)
                cands.append(f"https://{h}")
        to_probe, untested = cands[:MAX_PROBES], cands[MAX_PROBES:]
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda u: (u, *probe(client, u, handles)), to_probe))

    state = _load_state(state_file)
    working = [u for u, ok, _ in results if ok]
    dead_all = [(u, why) for u, ok, why in results if not ok]
    for u, ok, why in results:
        s = state.setdefault(_host(u), {})
        s["last_probe"], s["last_result"] = stamp, why
        s["last_ok" if ok else "last_fail"] = stamp
    # Prefer dead instances that were recently good (intermittent > never-worked).
    dead_all.sort(key=lambda t: state.get(_host(t[0]), {}).get("last_ok", ""), reverse=True)
    dead = dead_all[:MAX_DEAD_KEPT]
    # Untested only carries hosts already in the file — never pad with strangers.
    untested = [u for u in untested if u in before]

    after = working + untested + [u for u, _ in dead]
    added = [u for u in working if u not in before]
    dropped = [u for u in before if u not in after]
    summary = {
        "at": stamp, "working": [_host(u) for u in working],
        "dead": {_host(u): why for u, why in dead_all},
        "added": [_host(u) for u in added], "dropped": [_host(u) for u in dropped],
        "replaced_dead": len([u for u, _ in dead_all if u in before]),
        "first_before": _host(before[0]) if before else "",
        "first_after": _host(after[0]) if after else "",
        "changed": after != before, "dry_run": dry_run,
    }
    if not dry_run:
        path.write_text(render(working, untested, dead, never, stamp))
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2, sort_keys=True))
        with log_file.open("a") as f:
            f.write(f"[{stamp}] working={len(working)}/{len(results)} probed"
                    f" | first {summary['first_before'] or '-'} -> {summary['first_after'] or '-'}"
                    f" | added {summary['added'] or '-'} | dropped {summary['dropped'] or '-'}\n")
            for u, ok, why in results:
                f.write(f"    {'OK  ' if ok else 'DEAD'} {_host(u)}: {why}\n")
    return summary


def one_line(s: dict) -> str:
    return (f"x-instances: {len(s['working'])} working"
            + (f", +{','.join(s['added'])}" if s["added"] else "")
            + (f", dropped {','.join(s['dropped'])}" if s["dropped"] else "")
            + (f", {s['replaced_dead']} dead in old list" if s["replaced_dead"] else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="probe + report, do not rewrite")
    ap.add_argument("--file", type=Path, default=INSTANCES_FILE)
    ap.add_argument("--no-status", action="store_true", help="skip status.d420.de")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    s = heal(args.file, dry_run=args.dry_run, use_status=not args.no_status)
    print(json.dumps(s, indent=2) if args.json else one_line(s))
    return 0 if s["working"] else 2


if __name__ == "__main__":
    sys.exit(main())
