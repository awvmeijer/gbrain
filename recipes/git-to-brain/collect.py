"""Git commits → GBrain collector (deterministic; ports the archived ~/brain git ingest).

For each repo in repos.txt, walks `git log <cursor>.. --all` (cursor = last-seen
SHA, mirroring ~/brain/brain/ingest/git.py) and digests that day's NEW commits
into ONE markdown page per repo per day — sha-short, subject, author,
files-changed count per line. Same volume control as rss-to-brain: the ceiling
is one page per repo per day; re-runs rewrite today's page with a superset.

CURSOR INIT — nothing historical, ever. Historical commits are ALREADY in the
corpus as legacy-git_commit pages (the 2026-07-02 legacy replay). On the first
run for a repo the cursor is initialized to current HEAD and NO pages are
written; only commits made after that init flow in. A per-repo init timestamp
additionally filters out pre-init commits that `--all` can surface later (stale
unmerged branches are reachable-from-a-ref but never ancestors of the cursor).

- State: cursors + init timestamps + seen SHAs + recent-day commit snippets at
  ~/gbrain/logs/git-state.json (rss/edgar/federal convention — never inside
  ~/brains-ingest). Seen-SHA dedup makes re-runs safe regardless of cursor.
- Superset rewrites: recent-day snippets are kept in state, so a later run
  rewrites today's page with old+new commits (never loses lines). Commits dated
  outside the DAYS_KEPT window bucket under today, so every page we might touch
  stays rebuildable from state.
- No deps beyond stdlib (subprocess + git); no network unless --fetch is passed
  (`git fetch --all --prune`, best-effort — the old brain fetched for the NTU
  SSH mirror; local-only is the default posture now).

Usage:
    python collect.py <output_dir> [--max 200] [--fetch]
    # test-only: add a repo without touching repos.txt
    python collect.py <output_dir> --extra-repo "slug | /path/to/repo | -"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
STATE = HOME / "gbrain" / "logs" / "git-state.json"
HERE = Path(__file__).parent
DAYS_KEPT = 7    # days of commit snippets kept in state for superset rewrites
SEEN_CAP = 2000  # per-repo dedup-SHA cap (oldest dropped first)

# ASCII unit/record separators so subject/author can contain anything safely
# (same idiom as the old brain/ingest/git.py _PRETTY format).
_FS, _RS = "\x1f", "\x1e"
_PRETTY = f"--pretty=format:%H{_FS}%h{_FS}%at{_FS}%an{_FS}%s{_RS}"


def _repos(extra: list[str]) -> list[dict[str, str]]:
    out = []
    lines = (HERE / "repos.txt").read_text().splitlines() + list(extra)
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        slug, path, remote = (p.strip() for p in s.split("|", 2))
        canonical = remote if remote and remote != "-" else path
        out.append({"slug": slug, "path": path, "canonical": canonical})
    return out


def _git(path: str, args: list[str], timeout: int = 30) -> str:
    r = subprocess.run(
        ["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def _list_commits(path: str, cursor: str, cap: int) -> list[dict]:
    """New commits since `cursor` across --all refs, oldest→newest.

    `--shortstat` rides along in the same call; in the split-on-\\x1e stream the
    stat line for commit N lands at the head of chunk N+1, so each chunk's
    non-field lines are attached to the PREVIOUS commit. Merge commits emit no
    stat (files=0).
    """
    raw = _git(path, ["log", f"{cursor}..", "--all", "-n", str(cap), "--shortstat", _PRETTY])
    commits: list[dict] = []
    for part in raw.split(_RS):
        for line in part.splitlines():
            if _FS in line:
                sha, short, at, author, subject = line.split(_FS, 4)
                commits.append({
                    "sha": sha, "short": short, "ts": int(at) if at.isdigit() else 0,
                    "author": author, "subject": subject, "files": 0, "ins": 0, "dels": 0,
                })
            elif commits and "changed" in line:
                m = re.search(r"(\d+) files? changed", line)
                commits[-1]["files"] = int(m.group(1)) if m else 0
                m = re.search(r"(\d+) insertions?", line)
                commits[-1]["ins"] = int(m.group(1)) if m else 0
                m = re.search(r"(\d+) deletions?", line)
                commits[-1]["dels"] = int(m.group(1)) if m else 0
    commits.reverse()  # git log is newest-first; cursor we record = newest SHA
    return commits


def _bucket_day(ts: int, now: datetime) -> tuple[str, str]:
    """(day, HH:MM) for a commit — outside-the-state-window (or future) commits
    bucket under today so every page we might rewrite stays superset-safe."""
    if not ts:
        return now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt > now + timedelta(hours=1) or dt < now - timedelta(days=DAYS_KEPT - 1):
        return now.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def _write_page(out_root: Path, repo: dict, day: str, entries: list[dict]) -> None:
    lines = []
    for e in sorted(entries, key=lambda x: (x["hm"], x["short"])):
        line = f"- **{e['hm']}** `{e['short']}` {e['subject']} — {e['author']}"
        if e["files"]:
            line += f" ({e['files']} files, +{e['ins']}/-{e['dels']})"
        lines.append(line)
    body = (
        f"---\ntitle: Git {repo['slug']} — {day}\nsource: git\nrepo: \"{repo['canonical']}\"\n"
        f"slug: {repo['slug']}\ndate: {day}\ntags: [git, commits, {repo['slug']}]\n---\n\n"
        f"# Git {repo['slug']} — {day}\n\n" + "\n".join(lines) + "\n"
    )
    p = out_root / "git" / repo["slug"] / f"{day}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--max", type=int, default=200, help="max commits/repo to consider per run")
    ap.add_argument("--fetch", action="store_true", help="git fetch --all --prune first (best-effort)")
    ap.add_argument("--extra-repo", action="append", default=[],
                    help='TEST-ONLY: extra "slug | path | remote" line, not in repos.txt')
    args = ap.parse_args()

    out_root = Path(args.output_dir)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    rstate: dict[str, dict] = state.get("repos", {})
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=DAYS_KEPT)).strftime("%Y-%m-%d")

    repos = _repos(args.extra_repo)
    total_new, pages, ok, inited, failed = 0, 0, 0, [], []
    for repo in repos:
        slug = repo["slug"]
        try:
            if args.fetch:
                subprocess.run(["git", "-C", repo["path"], "fetch", "--all", "--prune"],
                               capture_output=True, timeout=60)
            head = _git(repo["path"], ["rev-parse", "HEAD"]).strip()
        except Exception as e:  # noqa: BLE001 — one bad repo never kills the run
            failed.append(f"{slug} ({type(e).__name__})")
            continue
        ok += 1
        rs = rstate.get(slug)
        if rs is None or not rs.get("cursor"):
            # FIRST RUN: cursor→HEAD, ingest nothing — history is already in the
            # corpus as legacy-git_commit pages; only post-init commits flow.
            rstate[slug] = {"cursor": head, "init_ts": int(now.timestamp()),
                            "seen": [], "days": {}}
            inited.append(f"{slug}@{head[:7]}")
            continue
        try:
            commits = _list_commits(repo["path"], rs["cursor"], args.max)
        except Exception:  # noqa: BLE001 — cursor lost (rebase/gc) → re-init at HEAD
            rs.update(cursor=head, init_ts=int(now.timestamp()))
            inited.append(f"{slug}@{head[:7]} (cursor lost; re-initialized)")
            continue
        seen = set(rs.get("seen", []))
        init_ts = int(rs.get("init_ts", 0))
        new_shas: list[str] = []
        days: dict[str, list[dict]] = rs.setdefault("days", {})
        dirty: set[str] = set()
        for c in commits:
            rs["cursor"] = c["sha"]  # commits arrive oldest→newest
            if c["sha"] in seen or c["ts"] < init_ts:
                continue  # dup, or a pre-init commit surfaced via a stale ref
            seen.add(c["sha"])
            new_shas.append(c["sha"])
            day, hm = _bucket_day(c["ts"], now)
            days.setdefault(day, []).append({
                "hm": hm, "short": c["short"], "subject": c["subject"],
                "author": c["author"], "files": c["files"], "ins": c["ins"], "dels": c["dels"],
            })
            dirty.add(day)
            total_new += 1
        for day in sorted(dirty):
            _write_page(out_root, repo, day, days[day])
            pages += 1
        for day in [d for d in days if d < cutoff]:
            del days[day]
        rs["seen"] = (rs.get("seen", []) + new_shas)[-SEEN_CAP:]

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"repos": rstate}))
    print(f"repos ok: {ok}/{len(repos)} | new commits: {total_new} | pages written: {pages}"
          + (f" | initialized: {', '.join(inited)}" if inited else "")
          + (f" | failed: {', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
