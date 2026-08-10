#!/usr/bin/env python3
"""Science / research layer → BRAINS (Sprint 6). Same convergence engine, research
domain: stay on top of the field (arXiv) and the group (OpenAlex — new papers BY
Steve Yim's group + new citations OF their work). All keyless.

Endpoints (free, no key; polite `mailto` for OpenAlex):
  - arXiv Atom : http://export.arxiv.org/api/query?search_query=cat:<cat>&sortBy=submittedDate
  - OpenAlex   : https://api.openalex.org/works?filter=author.id:<id>   (+ cited_by)

Writes `paper/*` + `author/*` pages with `authored_by` and `cited_by` edges — the
graph substrate the group-watch / field-weekly skills read. Dedup on work id
(state). Run: python recipes/science-to-brain/collect.py [--dry-run] [--max N]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
STATE = GBRAIN / "logs" / "science-state.json"
LOG = GBRAIN / "logs" / "science.log"
MAILTO = "awvmeijer@gmail.com"
UA = f"BRAINS/1.0 ({MAILTO})"

# The group + field to watch. Verified on OpenAlex (A5058562886 = Steve Hung Lam
# Yim, NTU, 223 works). Add co-authors / more categories as the graph grows.
OPENALEX_AUTHORS = {"A5058562886": "Steve Hung Lam Yim"}
ARXIV_CATS = ["physics.ao-ph"]  # atmospheric & oceanic physics — the group's field

DRY = "--dry-run" in sys.argv
MAX = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--max=")), 15)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"[science] {msg}", flush=True)
    with open(LOG, "a") as f:
        f.write(f"[science] {msg}\n")


def gb(args: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def http(url: str, is_json: bool = True):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if is_json else raw.decode("utf-8", "ignore")
    except Exception as e:
        log(f"  http {url[:56]} failed: {str(e)[:90]}")
        return None


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"seen": []}


def slugify(s: str, n: int = 56) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "x"


def _paper_page(slug, title, source, ident, date, venue, authors, extra=""):
    alist = ", ".join(authors[:8])
    return (f"---\ntype: paper\ntitle: \"{title[:120].replace(chr(34), chr(39))}\"\n"
            f"source: {source}\nident: \"{ident}\"\ndate: {date}\nvenue: \"{venue[:80].replace(chr(34), chr(39))}\"\n"
            f"tags: [paper, science]\n---\n\n# {title[:200]}\n\n"
            f"- **Source:** {source}  ·  **Date:** {date}  ·  **Venue:** {venue}\n"
            f"- **Authors:** {alist}\n- **Id:** `{ident}`\n{extra}\n")


def ensure_author(name: str, oa_id: str | None = None) -> str:
    slug = f"author/{slugify(name)}"
    rc, out = gb(["get", slug])
    if rc != 0 or not out.strip():
        gb(["put", slug, "--content",
            f"---\ntype: author\ntitle: {name}\nsource: openalex\n"
            f"openalex_id: {oa_id or ''}\ntags: [author, science]\n---\n\n# {name}\n\nResearcher.\n"])
    return slug


def do_openalex(seen: set, made: dict) -> None:
    for oa_id, name in OPENALEX_AUTHORS.items():
        url = (f"https://api.openalex.org/works?filter=author.id:{oa_id}"
               f"&sort=publication_date:desc&per-page={MAX}&mailto={MAILTO}")
        data = http(url)
        works = (data or {}).get("results", []) if isinstance(data, dict) else []
        log(f"  openalex {name}: {len(works)} recent works")
        author_slug = None if DRY else ensure_author(name, oa_id)
        for w in works:
            wid = (w.get("id") or "").split("/")[-1]
            key = f"oa:{wid}"
            if not wid or key in seen:
                continue
            title = w.get("title") or w.get("display_name") or "(untitled)"
            date = (w.get("publication_date") or "")[:10]
            venue = (((w.get("primary_location") or {}).get("source") or {}).get("display_name")) or ""
            auths = [a.get("author", {}).get("display_name", "") for a in (w.get("authorships") or [])]
            cited = w.get("cited_by_count", 0)
            slug = f"papers/oa-{slugify(wid)}"
            if DRY:
                log(f"    WOULD add paper {title[:60]} ({date}, cited {cited})")
                seen.add(key); continue
            content = _paper_page(slug, title, "openalex", wid, date, venue, auths,
                                  extra=f"- **Cited by:** {cited}\n")
            rc, _ = gb(["put", slug, "--content", content])
            if rc == 0:
                gb(["link", slug, author_slug, "--link-type", "authored_by", "--link-source", "openalex"])
                made["paper"] = made.get("paper", 0) + 1
                seen.add(key)
        # cross-over signal: who recently CITED the group's 3 most-recent works
        for w in works[:3]:
            wid = (w.get("id") or "").split("/")[-1]
            cby = http(f"https://api.openalex.org/works?filter=cites:{wid}"
                       f"&sort=publication_date:desc&per-page=4&mailto={MAILTO}")
            citing = (cby or {}).get("results", []) if isinstance(cby, dict) else []
            base_slug = f"papers/oa-{slugify(wid)}"
            for c in citing:
                cid = (c.get("id") or "").split("/")[-1]
                key = f"oa:{cid}"
                if not cid:
                    continue
                cslug = f"papers/oa-{slugify(cid)}"
                if key not in seen and not DRY:
                    ct = c.get("title") or "(untitled)"
                    cauth = [a.get("author", {}).get("display_name", "") for a in (c.get("authorships") or [])]
                    gb(["put", cslug, "--content", _paper_page(
                        cslug, ct, "openalex", cid, (c.get("publication_date") or "")[:10],
                        (((c.get("primary_location") or {}).get("source") or {}).get("display_name")) or "",
                        cauth)])
                    made["paper"] = made.get("paper", 0) + 1
                    seen.add(key)
                if not DRY:  # base paper --cited_by--> citing paper
                    gb(["link", base_slug, cslug, "--link-type", "cited_by", "--link-source", "openalex"])
                    made["cited_by"] = made.get("cited_by", 0) + 1


def do_arxiv(seen: set, made: dict) -> None:
    for cat in ARXIV_CATS:
        url = ("http://export.arxiv.org/api/query?search_query=cat:"
               f"{urllib.parse.quote(cat)}&sortBy=submittedDate&sortOrder=descending&max_results={MAX}")
        xml = http(url, is_json=False)
        if not xml:
            continue
        entries = re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL)
        log(f"  arxiv {cat}: {len(entries)} recent")
        for e in entries:
            aid_m = re.search(r"<id>(.*?)</id>", e)
            aid = (aid_m.group(1).strip().split("/abs/")[-1]) if aid_m else ""
            key = f"arxiv:{aid}"
            if not aid or key in seen:
                continue
            title = re.sub(r"\s+", " ", (re.search(r"<title>(.*?)</title>", e, re.DOTALL) or [None, ""])[1]).strip()
            date = (re.search(r"<published>(.*?)</published>", e) or [None, ""])[1][:10]
            auths = re.findall(r"<name>(.*?)</name>", e)
            slug = f"papers/arxiv-{slugify(aid)}"
            if DRY:
                log(f"    WOULD add arxiv {title[:60]} ({date})")
                seen.add(key); continue
            rc, _ = gb(["put", slug, "--content",
                        _paper_page(slug, title, "arxiv", aid, date, f"arXiv {cat}", auths,
                                    extra=f"- **arXiv:** {aid}\n")])
            if rc == 0:
                made["paper"] = made.get("paper", 0) + 1
                seen.add(key)


def main() -> None:
    st = load_state()
    seen = set(st.get("seen", []))
    made: dict = {}
    log(f"authors={list(OPENALEX_AUTHORS.values())} · arxiv={ARXIV_CATS} · prior seen={len(seen)}")
    do_openalex(seen, made)
    do_arxiv(seen, made)
    if not DRY:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"seen": sorted(seen)}))
    log(f"DONE — made={made}, total seen={len(seen)}")


if __name__ == "__main__":
    main()
