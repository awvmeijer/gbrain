#!/usr/bin/env python3
"""Tier-0 deterministic entity extractor (BRAINS Sprint 1).

Populates the relation graph from the fresh corpus WITHOUT any LLM. Reads the
local `~/brains-ingest/**/*.md` (fast — no per-page `gbrain get`), regex-scans
each page body for entities, ensures the target entity page exists, then writes
a typed edge via the sanctioned `gbrain link` op.

Why `gbrain link` and not the pack's inference/NER: gbrain's NER only targets
hardcoded person/company/organization types, pack `frontmatter_links` are inert,
and links to non-existent pages are dropped (FK NOT NULL). So the additive,
no-core-fork path is: pre-create entity pages + create edges explicitly.

Extractors (deterministic, "code for data"):
  - cashtag  `$SYM` (1-5 caps)      → ticker/<SYM>   , link  page --mentions--> ticker
  - SEC accession `##########-##-######` → filing/<acc> , link  page --files-->   filing   (source = ticker if a cashtag co-occurs)
  - arXiv id `arXiv:####.#####`       → paper/arxiv-<id>, link  page --mentions--> paper
  - DOI `10.####/...`                 → paper/doi-<slug>, link  page --mentions--> paper

Idempotent: a state file records created pages + (src,dst,type) edges, so re-runs
are no-ops. Run: `python recipes/entity-extract/backfill.py [--limit N] [--dry-run]`
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
ING = HOME / "brains-ingest"
STATE = GBRAIN / "logs" / "entity-extract-state.json"
LOG = GBRAIN / "logs" / "entity-extract.log"

# Case-insensitive: fintwit writes both `$NVDA` and `$vktx`. Uppercased + filtered
# below so lowercase real tickers wire the graph without letting `$word` emphasis
# mint junk nodes.
CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
ACCESSION = re.compile(r"\b(\d{10}-\d{2}-\d{6})\b")
ARXIV = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
# cashtags that are noise, not tradeable symbols the user cares about
CASHTAG_STOP = {
    "A", "I", "U", "USD", "AI", "CEO", "CFO", "IPO", "ATH", "YOLO", "EOD", "PM", "AM",
    "EPS", "GDP", "FED", "ER", "CPI", "PPI", "FOMC", "ETF", "YTD", "OI", "IV", "DD", "PT",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER", "WAS", "ONE",
    "OUR", "OUT", "DAY", "GET", "HAS", "NEW", "NOW", "SEE", "TWO", "WHO", "LOL", "IMO",
    "TBH", "FYI", "WTF", "OMG", "BIG", "BUY", "TOP", "LOW", "HOT", "RED",
}


def _cashtags(body: str):
    """Uppercased ticker symbols from `$SYM` (any case). Drops stopwords and drops
    1-char symbols unless they were UPPERCASE in source ($T Ford/AT&T kept, $t junk)."""
    for m in CASHTAG.finditer(body):
        raw = m.group(1)
        sym = raw.upper()
        if sym in CASHTAG_STOP:
            continue
        if len(sym) == 1 and not raw.isupper():
            continue
        yield sym

DRY = "--dry-run" in sys.argv
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[entity-extract] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def gb(args: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"pages": [], "edges": []}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def brain_slugs() -> set[str]:
    """Every live slug, from one read-only query. (`gbrain list` caps at 100 rows,
    which is why this used to fall back to a `gbrain get` per entity.)"""
    url = json.loads((HOME / ".gbrain" / "config.json").read_text())["database_url"]
    r = subprocess.run(["psql", url, "-At", "-v", "ON_ERROR_STOP=1", "-c",
                        "SELECT slug FROM pages WHERE deleted_at IS NULL"],
                       capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PGOPTIONS": "-c default_transaction_read_only=on"})
    if r.returncode != 0:
        raise SystemExit(f"[entity-extract] cannot list brain slugs: {r.stderr.strip()[:200]}")
    return {ln for ln in r.stdout.splitlines() if ln}


def page_slug(rel: str, slugs: set[str]) -> str | None:
    """The brain slug an ingest file was imported as, or None. Import lowercases and
    normalises hyphens (`capture/2026-07-01T152411-x` → `capture/2026-07-01t152411-x`,
    `telegram/1---scanner` → `telegram/1-scanner`, `youtube/c/-abc` → `youtube/c/abc`),
    so the raw relpath matched only ~15% of pages and every other link failed."""
    if rel in slugs:
        return rel
    low = rel.lower()
    if low in slugs:
        return low
    norm = "/".join(re.sub(r"-{2,}", "-", seg).strip("-") for seg in low.split("/"))
    return norm if norm in slugs else None


PAGE_TMPL = {
    "ticker": "---\ntype: ticker\ntitle: {t}\nsymbol: {t}\nsource: entity-extract\ntags: [ticker]\n---\n\n# {t}\n\nTicker hub. Filings, permits, catalysts, and feed mentions link here.\n",
    "filing": "---\ntype: filing\ntitle: SEC filing {t}\naccession: {t}\nsource: entity-extract\ntags: [filing, sec]\n---\n\n# SEC filing {t}\n\nAuto-created filing node (accession {t}).\n",
    "paper": "---\ntype: paper\ntitle: {t}\nsource: entity-extract\ntags: [paper]\n---\n\n# {t}\n\nAuto-created paper node.\n",
}


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def main() -> None:
    st = load_state()
    created = set(st["pages"])
    edges = set(tuple(e) for e in st["edges"])
    have = brain_slugs()
    log(f"existing pages: {len(have)}; prior created: {len(created)}; prior edges: {len(edges)}")

    files = sorted(ING.rglob("*.md"))
    if LIMIT:
        files = files[:LIMIT]

    # accumulate: entity slug -> (kind, template-token, set of source page slugs)
    ents: dict[str, tuple[str, str, set[str]]] = {}

    def add(ent_slug: str, kind: str, token: str, src: str, link_type: str):
        e = ents.setdefault(ent_slug, (kind, token, set()))
        e[2].add((src, link_type))

    scanned = unimported = 0
    for f in files:
        try:
            rel = f.relative_to(ING).as_posix()
        except ValueError:
            continue
        slug = page_slug(rel[:-3] if rel.endswith(".md") else rel, have)
        if slug is None:  # not imported (yet): nothing to link from
            unimported += 1
            continue
        body = f.read_text(errors="ignore")
        scanned += 1
        for sym in _cashtags(body):
            add(f"ticker/{sym.lower()}", "ticker", sym, slug, "mentions")
        for m in ACCESSION.finditer(body):
            acc = m.group(1)
            add(f"filings/{acc}", "filing", acc, slug, "files")
        for m in ARXIV.finditer(body):
            aid = m.group(1)
            add(f"papers/arxiv-{aid}", "paper", f"arXiv:{aid}", slug, "mentions")
        for m in DOI.finditer(body):
            doi = m.group(1)
            add(f"papers/doi-{slugify(doi)}", "paper", doi, slug, "mentions")

    n_ent = len(ents)
    n_edge = sum(len(v[2]) for v in ents.values())
    log(f"scanned {scanned} pages ({unimported} files not in the brain, skipped) → "
        f"{n_ent} entities, {n_edge} candidate edges")
    if DRY:
        top = sorted(ents.items(), key=lambda kv: -len(kv[1][2]))[:15]
        for slug, (kind, tok, srcs) in top:
            log(f"  {slug} ({kind}) ← {len(srcs)} mentions")
        return

    # 1) ensure entity pages exist
    made = 0
    for ent_slug, (kind, token, _srcs) in ents.items():
        if ent_slug in created or ent_slug in have:
            continue
        # `have` is every live slug, so an existing page (e.g. an EDGAR filing
        # page) is never overwritten with the stub template.
        content = PAGE_TMPL[kind].format(t=token)
        rc, out = gb(["put", ent_slug], stdin=content)
        if rc == 0:
            created.add(ent_slug); made += 1
            if made % 25 == 0:
                st["pages"] = sorted(created); save_state(st); log(f"  created {made} entity pages…")
        else:
            log(f"  put FAILED {ent_slug}: {out.strip()[:120]}")
    st["pages"] = sorted(created); save_state(st)
    log(f"entity pages created this run: {made}")

    # 2) create typed edges
    linked = failed = 0
    for ent_slug, (kind, token, srcs) in ents.items():
        if ent_slug not in created and ent_slug not in have:
            continue
        for (src, lt) in srcs:
            key = (src, ent_slug, lt)
            if key in edges:
                continue
            rc, out = gb(["link", src, ent_slug, "--link-type", lt, "--link-source", "tier0-regex"])
            if rc == 0:
                edges.add(key); linked += 1
                if linked % 50 == 0:
                    st["edges"] = [list(e) for e in edges]; save_state(st); log(f"  linked {linked} edges…")
            else:
                failed += 1
                if failed <= 20:  # a systematic failure must not bury the log (09-24: 2,229 lines)
                    err = [l for l in out.splitlines() if "UPGRADE_AVAILABLE" not in l and "self-upgrade" not in l]
                    log(f"  link FAILED {src} -> {ent_slug}: {' '.join(err).strip()[:160]}")
    st["edges"] = [list(e) for e in edges]; save_state(st)
    log(f"DONE — edges created this run: {linked}; failed: {failed}; "
        f"total pages={len(created)}, total edges={len(edges)}")


if __name__ == "__main__":
    main()
