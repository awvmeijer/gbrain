#!/usr/bin/env python3
"""Convergence detector (BRAINS Sprint 4, selective v2) — the "1+1=3" engine.

Fires a ranked `finding` when INDEPENDENT signal classes collide on one watched
ticker *and* the collision is recent + material. Scoring is deterministic +
explainable (house rule: "volume is never a signal" — class diversity + temporal
coincidence dominate, raw count is capped). Only the narrative is LLM (`gbrain
think`). One finding per (ticker, month); re-runs update in place.

Signal classes on a ticker hub:
  - fintwit : inbound `mentions` from feed pages (x/telegram/youtube/discord)
  - filing  : outbound `files` to SEC filing pages
  - permit  : inbound `subject_of` from permit pages   (Sprint 3, when present)
  - level   : inbound `tracks_level` from index_level    (Sprint 5, when present)

Score = base(class diversity) + recency + material-event bonus + event↔mention
coincidence (the tell) + capped burst + hold/watch. Fires when >= 2 classes and
score >= THRESHOLD.

Run: python recipes/convergence/detect.py [--only SYM] [--no-think] [--dry-run]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
LOG = GBRAIN / "logs" / "convergence.log"
THRESHOLD = 0.70
SIGNAL_CLASSES = ("fintwit", "filing", "permit", "federal", "level")
FEED_PREFIXES = ("x/", "telegram/", "youtube/", "discord/", "capture/")
EVENT_FORMS = ("8-K", "13D", "SC 13D", "S-1", "424B", "S-3", "6-K")  # material events

ONLY = None
if "--only" in sys.argv:
    i = sys.argv.index("--only")
    if i + 1 < len(sys.argv):
        ONLY = sys.argv[i + 1].lower()
NO_THINK = "--no-think" in sys.argv
DRY = "--dry-run" in sys.argv
TOP_N = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--top=")), 5)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"[convergence] {msg}", flush=True)
    with open(LOG, "a") as f:
        f.write(f"[convergence] {msg}\n")


def gb(args: list[str], stdin: str | None = None, timeout: int = 240) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def gj(args: list[str], default):
    _, out = gb(args)
    try:
        return json.loads(out)
    except Exception:
        return default


def _yaml_list(content: str, key: str) -> list[str]:
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


def watchlist():
    _, out = gb(["get", "finance/watchlist"])
    held = _yaml_list(out, "held")
    watched = _yaml_list(out, "watched")
    return held, [s for s in dict.fromkeys(held + watched) if re.fullmatch(r"[A-Z]{1,5}", s)]


def _slug_dates(slugs) -> list[date]:
    out = []
    for s in slugs:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            try:
                out.append(date.fromisoformat(m.group(1)))
            except ValueError:
                pass
    return out


def _filing_events(sym: str) -> list[tuple[date, str]]:
    """(date, form) from the ticker timeline."""
    _, out = gb(["timeline", f"ticker/{sym.lower()}"])
    ev = []
    for ln in out.splitlines():
        m = re.match(r"\s*(\d{4}-\d{2}-\d{2})\S*\s+([\w\-\. ]+?):", ln)
        if m:
            try:
                ev.append((date.fromisoformat(m.group(1)), m.group(2).strip()))
            except ValueError:
                pass
    return ev


def gather(sym: str):
    """Return (classes:{class:set(slugs)}, meta:{mention_dates, events})."""
    slug = f"ticker/{sym.lower()}"
    classes: dict[str, set] = {}
    mention_slugs: set[str] = set()
    for e in gj(["backlinks", slug], []):
        lt, src = e.get("link_type"), e.get("from_slug", "")
        if lt == "mentions" and any(src.startswith(p) for p in FEED_PREFIXES):
            classes.setdefault("fintwit", set()).add(src)
            mention_slugs.add(src)
        elif lt == "subject_of" and src.startswith("permit"):
            classes.setdefault("permit", set()).add(src)
        elif lt == "subject_of" and src.startswith("catalyst"):
            classes.setdefault("federal", set()).add(src)
        elif lt == "tracks_level":
            classes.setdefault("level", set()).add(src)
    for node in gj(["graph", slug, "--depth", "1"], []):
        if node.get("slug") != slug:
            continue
        for lk in node.get("links", []):
            if lk.get("link_type") == "files":
                classes.setdefault("filing", set()).add(lk.get("to_slug", ""))
    meta = {"mention_dates": _slug_dates(mention_slugs), "events": _filing_events(sym)}
    return classes, meta


def score(classes, meta, held, watched):
    active = [c for c in SIGNAL_CLASSES if classes.get(c)]
    n = len(active)
    if n < 2:
        return 0.0, active, 0, []
    today = date.today()
    reasons = [f"{n} independent signal classes"]
    base = 0.40 + 0.25 * (n - 2)  # 2→0.40, 3→0.65, 4→0.90 — class diversity is the dominant term

    all_dates = list(meta["mention_dates"]) + [d for d, _ in meta["events"]]
    days = min(((today - d).days for d in all_dates if d <= today), default=999)
    recency = 0.12 if days <= 7 else 0.08 if days <= 14 else 0.04 if days <= 30 else 0.0
    if recency:
        reasons.append(f"latest signal {days}d ago")

    ev_recent = [d for d, f in meta["events"] if any(f.startswith(p) for p in EVENT_FORMS) and (today - d).days <= 30]
    event = 0.08 if ev_recent else 0.0
    if event:
        reasons.append("recent material filing (8-K / S-1 / 13D / offering)")

    coincidence = 0.0
    if ev_recent and meta["mention_dates"]:
        le, lm = max(ev_recent), max(meta["mention_dates"])
        if abs((le - lm).days) <= 14 and (today - le).days <= 21 and (today - lm).days <= 21:
            coincidence = 0.12
            reasons.append("**material filing coincides with a fintwit burst** — the 1+1=3 tell")

    recent_m = [d for d in meta["mention_dates"] if (today - d).days <= 14]
    burst = min(0.04, 0.01 * len(recent_m))
    hold = 0.10 if held else (0.06 if watched else 0.0)
    total_ev = sum(len(classes[c]) for c in active)
    s = round(min(1.0, base + recency + event + coincidence + burst + hold), 2)
    return s, active, total_ev, reasons


CLASS_LABEL = {"fintwit": "fintwit chatter", "filing": "SEC filings",
               "permit": "permit / zoning", "federal": "federal awards", "level": "price levels"}


def fire(sym, classes, sc, active, total_ev, held, reasons):
    window = date.today().strftime("%Y-%m")
    fslug = f"finding/{window}-{sym.lower()}"
    tslug = f"ticker/{sym.lower()}"
    labels = " + ".join(CLASS_LABEL.get(c, c) for c in active)

    sig_md = []
    for c in active:
        ev = sorted(classes[c])
        sig_md.append(f"- **{CLASS_LABEL.get(c, c)}** ({len(ev)}): " + ", ".join(f"`{s}`" for s in ev[:6]) + (" …" if len(ev) > 6 else ""))
    reasons_md = "\n".join(f"- {r}" for r in reasons)

    narrative = "_(synthesis pending — run without --no-think)_"
    if not NO_THINK:
        log(f"  running think for {sym} (synthesis)…")
        cls = ", ".join(CLASS_LABEL.get(c, c) for c in active)
        q = (f"Synthesize the convergence thesis on ${sym}. Independent signal classes have collided: {cls}. "
             f"Taking the fintwit takes, SEC filings, permit/zoning records, federal awards, and price levels in the brain "
             f"TOGETHER, what do they imply? Weight the non-consensus signals (permits, federal contracts/grants) — those "
             f"are the edge. Be concrete and cite pages. Flag what is NOT yet confirmed.")
        rc, out = gb(["think", q, "--anchor", tslug], timeout=300)
        body = out.strip()
        lines = body.split("\n")
        if lines and lines[0].lstrip("# ").lower().startswith("synthesize"):
            body = "\n".join(lines[1:]).strip()
        if rc == 0 and len(body) > 40:
            narrative = body
    else:
        # preserve an existing good synthesis on fast re-runs
        _, prev = gb(["get", fslug])
        m = re.search(r"## Synthesis\s*(.+)$", prev, re.DOTALL)
        if m and len(m.group(1).strip()) > 60 and "synthesis pending" not in m.group(1) and "synthesis skipped" not in m.group(1):
            narrative = m.group(1).strip()

    content = f"""---
type: finding
title: "Convergence — ${sym}: {labels}"
ticker: {sym}
score: {sc}
classes: [{", ".join(active)}]
signal_count: {total_ev}
window: {window}
status: open
source: convergence-detector
---

# Convergence — ${sym}  ·  score {sc}

**{len(active)} independent signal classes** converge on ${sym} (a {"held" if held else "watched"} name).
Class diversity + temporal coincidence fired this — raw volume alone would not.

## Signals
{chr(10).join(sig_md)}

## Why it fired
{reasons_md}

The house rule holds: many mentions of a single class never fire a finding; it takes
corroboration across *independent* source classes, ideally coincident in time.

## Synthesis
{narrative}
"""
    if DRY:
        log(f"  WOULD fire {fslug} (score {sc})")
        return
    rc, out = gb(["put", fslug], stdin=content)
    if rc != 0:
        log(f"  put FAILED {fslug}: {out.strip()[:120]}"); return
    gb(["link", fslug, tslug, "--link-type", "catalyst_for", "--link-source", "convergence"])
    log(f"  FIRED {fslug} — score {sc}, classes {active}")


def main() -> None:
    held_syms, syms = watchlist()
    if ONLY:
        syms = [s for s in syms if s.lower() == ONLY] or [ONLY.upper()]
    log(f"scanning {len(syms)} watched tickers (threshold {THRESHOLD})")
    ranked = []
    for sym in syms:
        classes, meta = gather(sym)
        sc, active, total_ev, reasons = score(classes, meta, sym in held_syms, True)
        ranked.append((sc, sym, active, classes, total_ev, reasons))
    ranked.sort(key=lambda r: (-r[0], -len(r[2])))  # score, then class-diversity
    eligible = [r for r in ranked if r[0] >= THRESHOLD and len(r[2]) >= 2]
    top = eligible[:TOP_N]
    fired = 0
    for r in ranked:
        sc, sym, active, classes, total_ev, reasons = r
        state = "FIRE" if r in top else ("rank" if r in eligible else "skip")
        log(f"  {state} {sym}: score={sc} classes={ {c: len(classes.get(c, [])) for c in SIGNAL_CLASSES if classes.get(c)} }")
        if state == "FIRE":
            fire(sym, classes, sc, active, total_ev, sym in held_syms, reasons)
            fired += 1
    # keep the board = current top-N: close findings that dropped out this run
    if not DRY and not ONLY:
        fired_syms = {r[1].lower() for r in top}
        win = date.today().strftime("%Y-%m")
        _, lst = gb(["list", "--type", "finding", "--limit", "50"])
        for line in lst.splitlines():
            slug = line.split("\t")[0].strip()
            m = re.match(rf"finding/{win}-([a-z]+)$", slug)
            if m and m.group(1) not in fired_syms:
                gb(["delete", slug])
                log(f"  closed stale finding {slug}")
    log(f"DONE — {fired} finding(s) fired (top {TOP_N} of {len(eligible)} eligible / {len(syms)} scanned)")


if __name__ == "__main__":
    main()
