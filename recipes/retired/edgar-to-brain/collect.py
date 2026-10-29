#!/usr/bin/env python3
"""EDGAR → BRAINS (Sprint 2). Poll SEC filings for the watchlist tickers and
wire each new filing into the ticker hub as a typed `files` edge + timeline entry.

Source of truth for WHICH tickers = the Forge-owned `finance/watchlist` page
(held + watched); falls back to a seed list until Forge writes it.

Endpoints (all free, no key — SEC fair-access = descriptive UA + <=10 req/s):
  - ticker→CIK : https://www.sec.gov/files/company_tickers.json  (cached daily)
  - filings    : https://data.sec.gov/submissions/CIK##########.json

For each new filing of an interesting form: create `filings/<accession>` (type
filing), link `ticker/<sym> --files--> filings/<acc>`, and add a timeline entry
on the ticker page. Dedup on accession (state file) → re-run is a no-op.

Run: python recipes/edgar-to-brain/collect.py [--days N] [--cap N] [--dry-run]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
DATA = GBRAIN / "data" / "edgar"
STATE = GBRAIN / "logs" / "edgar-state.json"
LOG = GBRAIN / "logs" / "edgar.log"
UA = "BRAINS/1.0 awvmeijer@gmail.com"
CT_URL = "https://www.sec.gov/files/company_tickers.json"
SUB_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
MIN_INTERVAL = 0.15  # ~<=7 req/s, under the 10 req/s SEC cap

# forms that carry alpha for a retail trader (prefix match)
FORM_PREFIXES = ("8-K", "10-Q", "10-K", "4", "3", "SC 13D", "SC 13G", "13D", "13G",
                 "S-1", "S-3", "424B", "6-K", "DEF 14A", "SD")
SEED_WATCHLIST = ["FCEL", "NVDA", "RKLB", "ASTS", "HIMS", "NBIS", "IREN", "PLTR", "OUST", "MU", "AMD"]

DRY = "--dry-run" in sys.argv
REPROCESS = "--reprocess" in sys.argv  # re-put existing filing pages with enriched item labels
DAYS = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--days=")), 120)
CAP = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--cap=")), 20)
_last_req = [0.0]

# 8-K / 6-K material-event item codes → human labels (the actual alpha signal)
ITEM_LABELS = {
    "1.01": "Material Definitive Agreement", "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy or Receivership", "2.01": "Completion of Acquisition/Disposition",
    "2.02": "Results of Operations (earnings)", "2.03": "Direct Financial Obligation",
    "2.04": "Triggering Event on Obligation", "2.05": "Costs from Exit/Disposal",
    "2.06": "Material Impairment", "3.01": "Delisting / Listing-Rule Failure",
    "3.02": "Unregistered Sale of Equity", "3.03": "Modification to Security-Holder Rights",
    "4.01": "Change in Accountant", "4.02": "Non-Reliance on Prior Financials",
    "5.01": "Change in Control", "5.02": "Director/Officer Change",
    "5.03": "Amendment to Charter/Bylaws", "5.07": "Shareholder Vote Results",
    "7.01": "Reg FD Disclosure", "8.01": "Other Events", "9.01": "Exhibits",
}
_ITEM_BOILERPLATE = {"7.01", "9.01"}


def _items_desc(items_str: str) -> str:
    codes = [c.strip() for c in (items_str or "").split(",") if c.strip()]
    if not codes:
        return ""
    ordered = [c for c in codes if c not in _ITEM_BOILERPLATE] + [c for c in codes if c in _ITEM_BOILERPLATE]
    return "; ".join(ITEM_LABELS.get(c, f"Item {c}") for c in ordered[:3])


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[edgar] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def gb(args: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def http_json(url: str, retries: int = 3) -> dict | list | None:
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception as e:
            log(f"  http {url[:60]} attempt {attempt+1} failed: {str(e)[:80]}")
            time.sleep(1.5 * (attempt + 1))
    return None


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"seen": []}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def cik_map() -> dict[str, int]:
    DATA.mkdir(parents=True, exist_ok=True)
    cache = DATA / "company_tickers.json"
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < 86400
    if not fresh:
        d = http_json(CT_URL)
        if d:
            cache.write_text(json.dumps(d))
    try:
        d = json.loads(cache.read_text())
        return {v["ticker"].upper(): int(v["cik_str"]) for v in d.values()}
    except Exception:
        return {}


def _yaml_list(content: str, key: str) -> list[str]:
    """Parse a frontmatter list — inline `key: [a, b]` OR block `key:\\n  - a`."""
    lines = content.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(rf"^{re.escape(key)}:\s*(.*)$", ln)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            inner = rest[1:rest.rfind("]")] if "]" in rest else rest[1:]
            return [s.strip().strip("'\"").upper() for s in inner.split(",") if s.strip()]
        out = []
        for ln2 in lines[i + 1:]:
            mm = re.match(r"^\s*-\s*(.+)$", ln2)
            if mm:
                out.append(mm.group(1).strip().strip("'\"").upper())
            elif ln2.strip() == "":
                continue
            else:
                break
        return out
    return []


def load_watchlist() -> list[str]:
    _, out = gb(["get", "finance/watchlist"])
    syms = _yaml_list(out, "held") + _yaml_list(out, "watched")
    syms = [s for s in dict.fromkeys(syms) if re.fullmatch(r"[A-Z]{1,5}", s)]
    if not syms:
        log("watchlist page empty/missing → using seed list")
        return SEED_WATCHLIST
    return syms


FILING_TMPL = """---
type: filing
title: "{form} — {name}: {event}"
source: edgar
ticker: {sym}
cik: {cik}
form: {form}
event: "{event}"
items: "{items}"
filed: {filed}
accession: {acc}
url: {url}
tags: [filing, sec, {sym_low}]
---

# {form} — {name}

- **Ticker:** ${sym}  ·  **Form:** {form}  ·  **Filed:** {filed}
- **Event:** {event}
- **Accession:** `{acc}`
- **Filing index:** {url}
"""


def ensure_ticker(sym: str, name: str, created: set[str]) -> str:
    slug = f"ticker/{sym.lower()}"
    if slug in created:
        return slug
    rc, out = gb(["get", slug])
    if rc != 0 or not out.strip():
        content = (f"---\ntype: ticker\ntitle: {sym}\nsymbol: {sym}\nname: {name}\n"
                   f"source: edgar\ntags: [ticker]\n---\n\n# {sym} — {name}\n\n"
                   "Ticker hub. Filings, permits, catalysts, and feed mentions link here.\n")
        gb(["put", slug], stdin=content)
    created.add(slug)
    return slug


def main() -> None:
    st = load_state()
    seen = set(st.get("seen", []))
    cmap = cik_map()
    if not cmap:
        log("FATAL: could not load company_tickers map"); return
    watch = load_watchlist()
    cutoff = (date.today() - timedelta(days=DAYS)).isoformat()
    log(f"watchlist={watch} · window>={cutoff} · cap={CAP}/ticker · prior seen={len(seen)}")

    created_tickers: set[str] = set()
    new_pages = new_links = 0
    for sym in watch:
        cik = cmap.get(sym)
        if not cik:
            log(f"  {sym}: no CIK in map, skip"); continue
        sub = http_json(SUB_URL.format(cik=cik))
        if not sub:
            log(f"  {sym}: submissions fetch failed"); continue
        name = sub.get("name", sym)
        r = sub.get("filings", {}).get("recent", {})
        forms = r.get("form", []); dates = r.get("filingDate", [])
        accs = r.get("accessionNumber", []); docs = r.get("primaryDocument", [])
        descs = r.get("primaryDocDescription", [""] * len(forms))
        items_arr = r.get("items", [""] * len(forms))
        picked = 0
        for i in range(len(accs)):
            if picked >= CAP:
                break
            form, filed, acc = forms[i], dates[i], accs[i]
            if filed < cutoff:
                break  # recent[] is newest-first
            if not any(form.startswith(p) for p in FORM_PREFIXES):
                continue
            picked += 1
            already = acc in seen
            if already and not REPROCESS:
                continue
            acc_nodash = acc.replace("-", "")
            doc = docs[i] if i < len(docs) else ""
            items = items_arr[i] if i < len(items_arr) else ""
            human = _items_desc(items) or descs[i] or doc or form
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
            fslug = f"filings/{acc.lower()}"
            if DRY:
                log(f"  WOULD add {sym} {form} {filed} [{human}]")
                seen.add(acc); continue
            tslug = ensure_ticker(sym, name, created_tickers)
            content = FILING_TMPL.format(form=form, name=name, filed=filed, sym=sym,
                                         sym_low=sym.lower(), cik=cik, acc=acc, url=url,
                                         event=human.replace('"', "'"), items=items)
            rc, out = gb(["put", fslug], stdin=content)
            if rc != 0:
                log(f"  put FAILED {fslug}: {out.strip()[:100]}"); continue
            new_pages += 1
            if not already:  # link + timeline only on first ingest (avoid dup edges/entries)
                gb(["link", tslug, fslug, "--link-type", "files", "--link-source", "edgar"])
                gb(["timeline-add", tslug, filed, f"{form}: {human}"])
                new_links += 1
                seen.add(acc)
            if new_pages % 15 == 0:
                st["seen"] = sorted(seen); save_state(st); log(f"  …{new_pages} filings so far")
        log(f"  {sym} ({name}): {picked} in-window, cik={cik}")

    st["seen"] = sorted(seen); save_state(st)
    log(f"DONE — new filing pages={new_pages}, new links={new_links}, total seen={len(seen)}")


if __name__ == "__main__":
    main()
