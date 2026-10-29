#!/usr/bin/env python3
"""Federal disclosures → BRAINS (Sprint 3, alt-alpha). Keyless government APIs
that traders don't read: USAspending (contracts + grants) and openFDA (drug
approvals/recalls). Maps watchlist tickers → recipient/company names, pulls new
awards, and wires each as a `catalyst` page linked `subject_of` the ticker — so a
DOE fuel-cell grant or a Palantir contract becomes a convergence signal class.

Endpoints (all free, no key):
  - USAspending : POST https://api.usaspending.gov/api/v2/search/spending_by_award/
  - openFDA     : GET  https://api.fda.gov/drug/label.json?search=...

Watchlist is the Forge-owned `finance/watchlist` page. Dedup on award id (state
file) → re-run is a no-op. Run: python recipes/federal-to-brain/collect.py [--dry-run] [--since YYYY-MM-DD]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
STATE = GBRAIN / "logs" / "federal-state.json"
LOG = GBRAIN / "logs" / "federal.log"
UA = "BRAINS/1.0 awvmeijer@gmail.com"
USASPENDING = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

DRY = "--dry-run" in sys.argv
SINCE = next((a.split("=")[1] for a in sys.argv if a.startswith("--since=")), None)

# Ticker → USAspending recipient search text. Only names with plausible federal
# footprint are worth querying; the rest are skipped (no awards → no noise).
RECIPIENT = {
    "FCEL": "FuelCell Energy", "PLTR": "Palantir", "RKLB": "Rocket Lab",
    "ASTS": "AST SpaceMobile", "NVDA": "NVIDIA", "MU": "Micron",
    "AMD": "Advanced Micro Devices", "OUST": "Ouster", "IREN": "Iris Energy",
}
# USAspending requires award_type_codes from ONE group per request → query
# contracts and grants separately. `generated_internal_id` is auto-returned (not
# a requestable field — including it 422s).
AWARD_GROUPS = [["A", "B", "C", "D"], ["02", "03", "04", "05"]]
AWARD_FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Start Date", "Description"]


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"[federal] {msg}", flush=True)
    with open(LOG, "a") as f:
        f.write(f"[federal] {msg}\n")


def gb(args: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def http_json(url: str, body: dict | None = None) -> dict | list | None:
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    if data:
        hdr["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, headers=hdr, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"  http {url[:48]} failed: {str(e)[:100]}")
        return None


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"seen": []}


def _yaml_list(content: str, key: str) -> list[str]:
    for i, ln in enumerate(content.splitlines()):
        m = re.match(rf"^{re.escape(key)}:\s*(.*)$", ln)
        if m and m.group(1).strip().startswith("["):
            inner = m.group(1).strip()
            inner = inner[1:inner.rfind("]")] if "]" in inner else inner[1:]
            return [s.strip().strip("'\"").upper() for s in inner.split(",") if s.strip()]
    return []


def watchlist() -> list[str]:
    _, out = gb(["get", "finance/watchlist"])
    syms = _yaml_list(out, "held") + _yaml_list(out, "watched")
    return [s for s in dict.fromkeys(syms) if re.fullmatch(r"[A-Z]{1,5}", s)] or list(RECIPIENT)


def ensure_ticker(sym: str) -> str:
    slug = f"ticker/{sym.lower()}"
    rc, out = gb(["get", slug])
    if rc != 0 or not out.strip():
        gb(["put", slug, "--content",
            f"---\ntype: ticker\ntitle: {sym}\nsymbol: {sym}\nsource: federal\ntags: [ticker]\n---\n\n# {sym}\n\nTicker hub.\n"])
    return slug


CAT_TMPL = """---
type: catalyst
title: "{title}"
source: usaspending
ticker: {sym}
agency: "{agency}"
amount: {amount}
award_id: "{aid}"
date: {start}
tags: [catalyst, federal, usaspending, {sym_low}]
---

# {title}

- **Ticker:** ${sym}  ·  **Recipient:** {recipient}
- **Amount:** ${amount:,.0f}  ·  **Agency:** {agency}
- **Start:** {start}  ·  **Award:** `{aid}`
- **Description:** {desc}

Federal award — the non-consensus catalyst. ${sym}'s exposure to government
funding/contracts, surfaced from USAspending (a signal most traders never read).
"""


def poll_usaspending(sym: str, cutoff: str) -> list[dict]:
    name = RECIPIENT.get(sym)
    if not name:
        return []
    out: list[dict] = []
    for group in AWARD_GROUPS:
        body = {
            "filters": {
                "recipient_search_text": [name],
                "time_period": [{"start_date": cutoff, "end_date": date.today().isoformat()}],
                "award_type_codes": group,
            },
            "fields": AWARD_FIELDS,
            "page": 1, "limit": 10, "sort": "Award Amount", "order": "desc",
        }
        resp = http_json(USASPENDING, body)
        if isinstance(resp, dict):
            out.extend(resp.get("results", []))
    return out


def main() -> None:
    st = load_state()
    seen = set(st.get("seen", []))
    cutoff = SINCE or (date.today() - timedelta(days=365)).isoformat()
    syms = watchlist()
    log(f"watchlist={syms} · since={cutoff} · mapped={[s for s in syms if s in RECIPIENT]}")

    new_pages = 0
    for sym in syms:
        results = poll_usaspending(sym, cutoff)
        if not results:
            continue
        for r in results:
            aid = str(r.get("generated_internal_id") or r.get("Award ID") or "")
            if not aid or aid in seen:
                continue
            amount = float(r.get("Award Amount") or 0)
            if amount < 100_000:  # skip tiny awards (noise)
                continue
            recipient = str(r.get("Recipient Name") or RECIPIENT[sym])
            # recipient_search_text is a loose substring match — "Micron" pulls in
            # "College of MICRONesia". Require the company's lead token as a WHOLE
            # WORD in the returned name (\bMICRON\b rejects MICRONESIA).
            tok = re.escape(RECIPIENT[sym].split()[0].upper())
            if not re.search(rf"\b{tok}\b", recipient.upper()):
                continue
            agency = str(r.get("Awarding Agency") or "").replace('"', "'")
            desc = str(r.get("Description") or "").replace('"', "'")[:400] or "(no description)"
            start = str(r.get("Start Date") or date.today().isoformat())[:10]
            title = f"{sym} federal award — ${amount:,.0f} {agency}"[:90].replace('"', "'")
            slug = f"catalyst/usasp-{re.sub(r'[^a-z0-9]+', '-', aid.lower())[:48]}"
            if DRY:
                log(f"  WOULD add {sym}: ${amount:,.0f} {agency} ({start})")
                seen.add(aid); continue
            tslug = ensure_ticker(sym)
            content = CAT_TMPL.format(title=title, sym=sym, sym_low=sym.lower(), agency=agency,
                                      amount=amount, aid=aid, start=start, recipient=recipient, desc=desc)
            rc, out = gb(["put", slug, "--content", content])
            if rc != 0:
                log(f"  put FAILED {slug}: {out.strip()[:100]}"); continue
            gb(["link", slug, tslug, "--link-type", "subject_of", "--link-source", "usaspending"])
            gb(["timeline-add", tslug, start, f"Federal award: ${amount:,.0f} {agency}"])
            seen.add(aid); new_pages += 1
            log(f"  + {sym}: ${amount:,.0f} {agency} ({start})")

    st["seen"] = sorted(seen)
    if not DRY:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st))
    log(f"DONE — {new_pages} new federal catalyst pages, total seen={len(seen)}")


if __name__ == "__main__":
    main()
