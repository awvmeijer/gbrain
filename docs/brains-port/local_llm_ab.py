#!/usr/bin/env python3
"""Local-LLM A/B for the reasoning/extraction tier: Gemma 4 vs incumbent qwen3.

Runs real feed pages (~/brains-ingest) through each model on the structured
ticker-extraction task the tier actually does, measuring the three things that
matter for this role: JSON-parse success rate, throughput (tokens/sec), and
latency. Deep synthesis stays on Claude — this is only the local extraction tier.

Usage: sidecars/.venv/bin/python docs/brains-port/local_llm_ab.py [n_pages]
"""
import glob
import json
import os
import sys
import time

import httpx

OLLAMA = "http://localhost:11434/api/chat"
MODELS = ["qwen3:30b-a3b", "gemma4:26b"]  # incumbent first
ING = os.path.expanduser("~/brains-ingest")

PROMPT = (
    "You are a finance-feed extractor. From the content below, extract every stock "
    "ticker mentioned and the author's stance. Return STRICT JSON only, no prose, no "
    "markdown fences:\n"
    '{"tickers":[{"symbol":"NBIS","direction":"bullish|bearish|watch|none","note":"short reason"}]}\n'
    'If no tickers appear, return {"tickers":[]}.\n\nCONTENT:\n'
)


def pages(n: int):
    files: list[str] = []
    for sub in ("x", "telegram", "youtube", "discord", "capture"):
        files += sorted(glob.glob(f"{ING}/{sub}/**/*.md", recursive=True))
    out = []
    for f in files:
        try:
            t = open(f).read()
        except Exception:
            continue
        if len(t) > 200:
            out.append((f, t[:4000]))
        if len(out) >= n:
            break
    return out


def call(model: str, content: str) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT + content}],
        "format": "json",
        "stream": False,
        "think": False,  # qwen3: suppress <think>; ignored by gemma
        "options": {"num_ctx": 8192, "temperature": 0},
    }
    t0 = time.time()
    try:
        d = httpx.post(OLLAMA, json=body, timeout=240).json()
    except Exception as e:
        return {"ok": False, "tps": 0, "dt": time.time() - t0, "ntick": 0, "err": str(e)[:60]}
    dt = time.time() - t0
    txt = (d.get("message") or {}).get("content", "")
    ec, ed = d.get("eval_count") or 0, d.get("eval_duration") or 1
    tps = ec / (ed / 1e9) if ed else 0
    ok, ntick = False, 0
    try:
        j = json.loads(txt)
        ok = isinstance(j.get("tickers"), list)
        ntick = len(j.get("tickers") or [])
    except Exception:
        ok = False
    return {"ok": ok, "tps": tps, "dt": dt, "ntick": ntick}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    ps = pages(n)
    print(f"# Local-LLM A/B — {len(ps)} feed pages, ticker-extraction (format=json)\n")
    table = {}
    for m in MODELS:
        call(m, "warm up")  # load model into memory, untimed
        rs = [call(m, c) for _f, c in ps]
        okp = round(sum(1 for r in rs if r["ok"]) / len(rs) * 100, 1)
        tps = round(sum(r["tps"] for r in rs) / len(rs), 1)
        dt = round(sum(r["dt"] for r in rs) / len(rs), 1)
        tick = sum(r["ntick"] for r in rs)
        table[m] = {"json_ok_pct": okp, "avg_tokens_per_s": tps, "avg_latency_s": dt, "tickers_found": tick}
        print(f"{m}: {table[m]}")
    print("\n" + json.dumps(table, indent=2))


if __name__ == "__main__":
    main()
