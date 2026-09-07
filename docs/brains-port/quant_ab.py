#!/usr/bin/env python3
"""Quantisation A/B for the reasoning/extraction tier — gemma4:26b at q4 / qat / q8.

Sibling of local_llm_ab.py (2026-07-01), which settled MODEL choice at a fixed
Q4_K_M. This settles the QUANTISATION axis, which that A/B never varied.

Method change vs the original: on this task every gemma variant emits clean JSON,
so json_ok% cannot discriminate. We therefore treat **q8_0 as the quality
reference** and score each cheaper quant by how well its extracted ticker SET
agrees with q8 on the same page (micro-averaged precision/recall/F1 over symbols).
Speed and latency are reported as before. Same prompt, same pages, temperature 0.

There is no q6 build of gemma4:26b in the ollama library; the ladder is
q4_K_M -> qat -> q8_0 -> bf16. bf16 (52GB) is excluded: it would swap on a 64GB
box that is also running Postgres, the reranker and the capture sidecar, and a
swap-bound latency figure measures the SSD, not the model.

Usage: sidecars/.venv/bin/python quant_ab.py [n_pages]
"""
import glob, json, os, sys, time
import httpx

OLLAMA = "http://localhost:11434/api/chat"
REFERENCE = "gemma4:26b-a4b-it-q8_0"
MODELS = ["gemma4:26b", REFERENCE]  # qat needs a newer ollama; see notes
ING = os.path.expanduser("~/brains-ingest")

PROMPT = (
    "You are a finance-feed extractor. From the content below, extract every stock "
    "ticker mentioned and the author's stance. Return STRICT JSON only, no prose, no "
    "markdown fences:\n"
    '{"tickers":[{"symbol":"NBIS","direction":"bullish|bearish|watch|none","note":"short reason"}]}\n'
    'If no tickers appear, return {"tickers":[]}.\n\nCONTENT:\n'
)

def pages(n):
    files = []
    for sub in ("x", "telegram", "youtube", "discord", "capture"):
        files += sorted(glob.glob(f"{ING}/{sub}/**/*.md", recursive=True))
    out = []
    for f in files:
        try: t = open(f).read()
        except Exception: continue
        if len(t) > 200: out.append((f, t[:4000]))
        if len(out) >= n: break
    return out

def call(model, content):
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT + content}],
            "format": "json", "stream": False, "think": False,
            "options": {"num_ctx": 8192, "temperature": 0}}
    t0 = time.time()
    try:
        d = httpx.post(OLLAMA, json=body, timeout=300).json()
    except Exception as e:
        return {"ok": False, "tps": 0, "dt": time.time()-t0, "syms": set(), "err": str(e)[:60]}
    dt = time.time()-t0
    txt = (d.get("message") or {}).get("content", "")
    ec, ed = d.get("eval_count") or 0, d.get("eval_duration") or 1
    tps = ec/(ed/1e9) if ed else 0
    ok, syms = False, set()
    try:
        j = json.loads(txt); lst = j.get("tickers")
        ok = isinstance(lst, list)
        syms = {str(t.get("symbol","")).upper().strip() for t in lst if isinstance(t, dict)} - {""}
    except Exception:
        ok = False
    return {"ok": ok, "tps": tps, "dt": dt, "syms": syms}

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    ps = pages(n)
    print(f"# Quantisation A/B — gemma4:26b — {len(ps)} feed pages, ticker extraction")
    print(f"# reference for agreement scoring: {REFERENCE}\n", flush=True)

    per = {}
    for m in MODELS:
        print(f"[run] {m} ...", flush=True)
        call(m, "warm up")                       # load into memory, untimed
        per[m] = [call(m, c) for _f, c in ps]

    ref = per[REFERENCE]
    rows = {}
    for m in MODELS:
        rs = per[m]
        tp = fp = fn = 0
        for r, rr in zip(rs, ref):
            tp += len(r["syms"] & rr["syms"]); fp += len(r["syms"] - rr["syms"]); fn += len(rr["syms"] - r["syms"])
        prec = tp/(tp+fp) if tp+fp else 1.0
        rec  = tp/(tp+fn) if tp+fn else 1.0
        f1   = 2*prec*rec/(prec+rec) if prec+rec else 0.0
        rows[m] = {
            "json_ok_pct": round(sum(1 for r in rs if r["ok"])/len(rs)*100, 1),
            "symbols_found": sum(len(r["syms"]) for r in rs),
            "agree_precision": round(prec, 3), "agree_recall": round(rec, 3),
            "agree_f1_vs_q8": round(f1, 3),
            "avg_tokens_per_s": round(sum(r["tps"] for r in rs)/len(rs), 1),
            "avg_latency_s": round(sum(r["dt"] for r in rs)/len(rs), 2),
        }
    print()
    hdr = f'{"model":<28}{"json%":>7}{"syms":>7}{"F1vsQ8":>9}{"tok/s":>8}{"lat s":>8}'
    print(hdr); print("-"*len(hdr))
    for m in MODELS:
        r = rows[m]
        print(f'{m:<28}{r["json_ok_pct"]:>7}{r["symbols_found"]:>7}{r["agree_f1_vs_q8"]:>9}{r["avg_tokens_per_s"]:>8}{r["avg_latency_s"]:>8}')
    print("\n" + json.dumps(rows, indent=2))

if __name__ == "__main__":
    main()
