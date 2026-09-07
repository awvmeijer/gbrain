"""Open pull requests → GBrain collector (deterministic; sibling of git-to-brain).

git-to-brain digests the commit STREAM (append-only events, cursor-based). Open
PRs are the opposite shape: current STATE. So this keeps no cursor and no seen-set
— each run rewrites today's page from the live `gh` answer, which is idempotent by
construction. Volume control matches the sibling: one page per repo per day, and
no page at all when a repo has no open PRs (an empty page every day is corpus noise).

The point of this collector is the stall signal. `age_d` (days since the PR was
opened) and `idle_d` (days since it was last touched) are what "surface stalls
before they set" is actually measured on, so both lead every line.

Repos come from ../git-to-brain/repos.txt — one source of truth for what counts as
Anthony's products. Rows whose remote is "-" (no origin) are skipped, not errored:
there is no PR surface to query. One failing repo never kills the run, same posture
as the sibling collector.

No deps beyond stdlib + the `gh` CLI (already authenticated with `repo` scope).

Usage:
    python collect.py <output_dir> [--state open|all] [--limit 50]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
REPOS = HERE.parent / "git-to-brain" / "repos.txt"


def _repos() -> list[dict[str, str]]:
    """Parse the sibling's repos.txt. Same format: slug | path | remote."""
    out = []
    for line in REPOS.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        slug, path, remote = (p.strip() for p in s.split("|", 2))
        if not remote or remote == "-":
            continue  # no origin → no PR surface to query
        out.append({"slug": slug, "path": path, "canonical": remote})
    return out


def _nwo(remote: str) -> str | None:
    """github.com owner/name from an https or ssh remote; None if not GitHub."""
    m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)", remote)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _prs(nwo: str, state: str, limit: int) -> list[dict]:
    r = subprocess.run(
        ["gh", "pr", "list", "--repo", nwo, "--state", state, "--limit", str(limit),
         "--json", "number,title,author,createdAt,updatedAt,isDraft,"
                   "reviewDecision,additions,deletions,headRefName,url"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or f"gh exit {r.returncode}")
    return json.loads(r.stdout or "[]")


def _days(iso: str, now: datetime) -> int:
    return max(0, (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days)


def _write_page(out_root: Path, repo: dict, day: str, prs: list[dict], now: datetime) -> None:
    lines = []
    for p in sorted(prs, key=lambda x: x["number"]):
        age, idle = _days(p["createdAt"], now), _days(p["updatedAt"], now)
        bits = [f"opened {p['createdAt'][:10]}", f"**{age}d old**", f"idle {idle}d"]
        if p.get("isDraft"):
            bits.append("DRAFT")
        if p.get("reviewDecision"):
            bits.append(p["reviewDecision"].lower().replace("_", " "))
        author = (p.get("author") or {}).get("login", "?")
        lines.append(
            f"- **#{p['number']}** {p['title']} — {author} · " + " · ".join(bits)
            + f" · +{p.get('additions', 0)}/-{p.get('deletions', 0)}"
            + f" · branch `{p.get('headRefName', '?')}` · {p.get('url', '')}"
        )
    # `slug:` is reserved by the importer (must equal the path-derived slug), so the
    # repo short name rides on repo_slug: — same constraint as git-to-brain.
    body = (
        f"---\ntitle: Open PRs {repo['slug']} — {day}\nsource: git\n"
        f"repo: \"{repo['canonical']}\"\nrepo_slug: {repo['slug']}\ndate: {day}\n"
        f"tags: [git, prs, {repo['slug']}]\n---\n\n"
        f"# Open PRs {repo['slug']} — {day}\n\n" + "\n".join(lines) + "\n"
    )
    p = out_root / "git" / repo["slug"] / f"prs-{day}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir")
    ap.add_argument("--state", default="open", choices=["open", "all"])
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    out_root = Path(args.output_dir)
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    pages, total, empty, failed = 0, 0, [], []

    for repo in _repos():
        nwo = _nwo(repo["canonical"])
        if not nwo:
            continue  # non-GitHub remote
        try:
            prs = _prs(nwo, args.state, args.limit)
        except Exception as e:  # noqa: BLE001 — one bad repo never kills the run
            failed.append(f"{repo['slug']} ({type(e).__name__})")
            continue
        if not prs:
            empty.append(repo["slug"])
            continue
        _write_page(out_root, repo, day, prs, now)
        pages += 1
        total += len(prs)

    print(f"[pr-to-brain] {pages} page(s), {total} open PR(s); "
          f"no-PRs: {', '.join(empty) or 'none'}"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
