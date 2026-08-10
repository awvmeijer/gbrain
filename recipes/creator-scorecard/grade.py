#!/usr/bin/env python3
"""Grade extracted calls against actual daily closes.

Price source: Yahoo Finance chart API (free, no key; UA header required),
  https://query1.finance.yahoo.com/v8/finance/chart/<T>?range=1y&interval=1d
cached to cache/<TICKER>.json (normalized [date, close] pairs) so re-runs are
offline. ~1 request/second. Fallback per ticker when Yahoo has nothing:
  https://stooq.com/q/d/l/?s=<t>.us&i=d  (free CSV; plain US equities only).
Unknown tickers are skipped gracefully and cached as empty so we never
re-hammer the API for them.

Grading rules:
  entry  = nearest close ON/AFTER the call date (<=5 calendar days later —
           weekend/holiday tolerance; beyond that the call is ungradeable).
  exit@h = nearest close ON/AFTER call date + h days (h ∈ {7, 30} calendar
           days, i.e. trading-day approximation; <=7-day tolerance).
  bullish correct  ⟺ exit > entry;  bearish correct ⟺ exit < entry.
  pending          ⟺ price history simply hasn't reached the horizon yet.
  excess = dir * (r_ticker - r_SPY) over the SAME entry/exit dates, where
           dir = +1 bullish / -1 bearish. A bull-market source that only ever
           matches SPY earns ~0 excess — that's the point.

Output: graded.jsonl (one line per call, per-horizon results embedded).
Usage: grade.py [--refresh]   (--refresh ignores the cache)
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CALLS = HERE / "calls.jsonl"
OUT = HERE / "graded.jsonl"

HORIZONS = (7, 30)
ENTRY_TOL = 5   # max calendar days after call date for the entry close
EXIT_TOL = 7    # max calendar days after target date for the exit close
# NB: Yahoo 429s full browser UA strings from scripts; the bare token passes.
UA = {"User-Agent": "Mozilla/5.0"}
BENCH = "SPY"

# Symbols the sources use that Yahoo spells differently.
ALIASES = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "DOGE": "DOGE-USD",
    "XRP": "XRP-USD", "SPX": "^GSPC", "NDX": "^NDX", "SOX": "^SOX",
    "VIX": "^VIX", "DJI": "^DJI", "RUT": "^RUT", "BRK.B": "BRK-B",
}

_last_fetch = 0.0


def _polite() -> None:
    global _last_fetch
    wait = 1.0 - (time.monotonic() - _last_fetch)
    if wait > 0:
        time.sleep(wait)
    _last_fetch = time.monotonic()


def _yahoo(sym: str) -> tuple[str, list[tuple[str, float]] | None]:
    """-> ("ok", data) | ("nodata", None) definitive | ("blocked", None) transient."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    for backoff in (0, 15, 45):            # 429 backoff ladder
        if backoff:
            time.sleep(backoff)
        _polite()
        try:
            r = httpx.get(url, params={"range": "1y", "interval": "1d"},
                          headers=UA, timeout=30)
        except httpx.HTTPError:
            continue
        if r.status_code == 429:
            continue
        if r.status_code == 404:
            return "nodata", None
        if r.status_code != 200:
            return "blocked", None
        res = (r.json().get("chart", {}).get("result") or [None])[0]
        if not res:
            return "nodata", None          # 200 + error body: Yahoo doesn't know it
        ts = res.get("timestamp") or []
        closes = ((res.get("indicators", {}).get("quote") or [{}])[0]
                  .get("close") or [])
        out = [(time.strftime("%Y-%m-%d", time.gmtime(t)), round(c, 4))
               for t, c in zip(ts, closes) if c is not None]
        return ("ok", out) if out else ("nodata", None)
    return "blocked", None


def _stooq(sym: str) -> list[tuple[str, float]] | None:
    if not sym.isalpha():                  # indices/crypto: no .us mapping
        return None
    _polite()
    try:
        r = httpx.get("https://stooq.com/q/d/l/",
                      params={"s": f"{sym.lower()}.us", "i": "d"},
                      headers=UA, timeout=30, follow_redirects=True)
        if r.status_code != 200 or "Date,Open" not in r.text:
            return None
        rows = list(csv.DictReader(io.StringIO(r.text)))
        out = [(row["Date"], float(row["Close"])) for row in rows if row.get("Close")]
        return out or None
    except (httpx.HTTPError, ValueError):
        return None


def closes_for(ticker: str, refresh: bool) -> tuple[str, list[tuple[str, float]]]:
    """Return (provider, [(date, close), ...]); cached on disk."""
    sym = ALIASES.get(ticker, ticker)
    cpath = CACHE / f"{ticker}.json"
    if cpath.exists() and not refresh:
        blob = json.loads(cpath.read_text())
        return blob["provider"], [tuple(x) for x in blob["closes"]]
    status, data = _yahoo(sym)
    provider = "yahoo"
    if status != "ok":
        data = _stooq(sym)
        provider = "stooq"
    if data is None:
        if status == "blocked":            # transient: do NOT cache as unknown
            raise RuntimeError(f"price API blocked while fetching {sym}; "
                               "re-run later (partial cache is kept)")
        data, provider = [], "none"
    cpath.write_text(json.dumps({"provider": provider, "symbol": sym,
                                 "fetched": date.today().isoformat(),
                                 "closes": data}))
    return provider, data


def close_on_or_after(closes: list[tuple[str, float]], d: str,
                      tol: int) -> tuple[str, float] | None:
    limit = (date.fromisoformat(d) + timedelta(days=tol)).isoformat()
    for cd, c in closes:                   # closes are date-sorted
        if cd >= d:
            return (cd, c) if cd <= limit else None
    return None


def grade_call(call: dict, closes, spy) -> dict:
    out = dict(call)
    d0 = call["date"]
    entry = close_on_or_after(closes, d0, ENTRY_TOL)
    if entry is None:
        out["status"] = "no_price_data"
        return out
    out["status"] = "graded"
    out["entry_date"], out["entry"] = entry
    sign = 1 if call["direction"] == "bullish" else -1
    last = closes[-1][0]
    for h in HORIZONS:
        tgt = (date.fromisoformat(d0) + timedelta(days=h)).isoformat()
        ex = close_on_or_after(closes, tgt, EXIT_TOL)
        if ex is None:
            out[f"h{h}"] = {"status": "pending" if tgt > last else "no_exit_data"}
            continue
        r = ex[1] / entry[1] - 1
        res = {"status": "graded", "exit_date": ex[0], "exit": ex[1],
               "return": round(r, 4), "hit": sign * (ex[1] - entry[1]) > 0}
        se = close_on_or_after(spy, entry[0], ENTRY_TOL)
        sx = close_on_or_after(spy, ex[0], EXIT_TOL)
        if se and sx and se[1]:
            res["excess"] = round(sign * (r - (sx[1] / se[1] - 1)), 4)
        out[f"h{h}"] = res
    return out


def main() -> int:
    refresh = "--refresh" in sys.argv
    CACHE.mkdir(exist_ok=True)
    calls = [json.loads(l) for l in CALLS.open()]
    tickers = sorted({c["ticker"] for c in calls})

    print(f"{len(calls)} calls, {len(tickers)} tickers (+{BENCH} benchmark)")
    _, spy = closes_for(BENCH, refresh)
    if not spy:
        print("FATAL: no benchmark (SPY) data", file=sys.stderr)
        return 1

    series, providers = {}, {"yahoo": 0, "stooq": 0, "none": 0}
    for i, t in enumerate(tickers, 1):
        provider, data = closes_for(t, refresh)
        providers[provider] += 1
        series[t] = data
        if not data:
            print(f"  [{i}/{len(tickers)}] {t}: no data (skipping its calls)")

    graded = [grade_call(c, series[c["ticker"]], spy) if series[c["ticker"]]
              else {**c, "status": "unknown_ticker"} for c in calls]
    with OUT.open("w") as f:
        for g in graded:
            f.write(json.dumps(g) + "\n")

    n_ok = sum(1 for g in graded if g["status"] == "graded")
    n30 = sum(1 for g in graded if g.get("h30", {}).get("status") == "graded")
    n7 = sum(1 for g in graded if g.get("h7", {}).get("status") == "graded")
    pend = sum(1 for g in graded
               for h in HORIZONS if g.get(f"h{h}", {}).get("status") == "pending")
    print(f"providers: {providers}")
    print(f"graded entries: {n_ok}/{len(calls)}  (@7d: {n7}, @30d: {n30}, "
          f"pending horizon-slots: {pend}) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
