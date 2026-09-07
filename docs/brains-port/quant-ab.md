# Quantisation A/B — reasoning/extraction tier (2026-09-07)

Sibling of `local-llm-ab.md` (2026-07-01), which chose **gemma4:26b** over
qwen3:30b-a3b at a fixed Q4_K_M. That A/B never varied quantisation. This one does.

## Available ladder

`gemma4:26b` has **no q6 build**. The ollama library offers:

| tag | size |
|---|---|
| `26b` (= `a4b-it-q4_K_M`) | 17–19 GB |
| `26b-a4b-it-qat` | 16 GB |
| `26b-a4b-it-q8_0` | 28 GB |
| `26b-a4b-it-bf16` | 52 GB |

`bf16` excluded: 52 GB on a 64 GB box also running Postgres, the reranker and the
capture sidecar would swap, and a swap-bound latency figure measures the SSD.
`qat` **could not be tested** — it requires a newer ollama than the installed
0.21.0_1 ("The model you are attempting to pull requires a newer version").

## Method

`quant_ab.py`, 30 real feed pages from `~/brains-ingest`, same prompt and
`temperature=0` as the original. Method change: every gemma variant emits clean
JSON here, so `json_ok%` cannot discriminate. **q8_0 is therefore the quality
reference**, and each cheaper quant is scored by how well its extracted ticker SET
agrees with q8 on the same page (micro-averaged precision/recall/F1 over symbols).

## Result

| model | size | JSON ok | symbols | F1 vs q8 | tok/s | avg latency |
|---|---|---|---|---|---|---|
| **gemma4:26b (Q4_K_M)** | 17 GB | 100% | 173 | **0.997** | **69.7** | **3.23 s** |
| gemma4:26b-a4b-it-q8_0 | 28 GB | 100% | 174 | 1.000 | 51.8 | 4.12 s |

Precision 1.000, recall 0.994 — across 30 pages q4 missed exactly **one** symbol
that q8 found, and produced **no** spurious ones.

## Decision: stay on Q4_K_M. No config change.

Q8 buys one ticker in 30 pages and costs 35% throughput, 28% latency, and 11 GB of
resident memory. For a high-volume extraction tier that is a loss on every axis.
Quantisation is not where quality lives for this task — model choice was
(100% vs 83% clean JSON in the 2026-07-01 A/B); precision beyond q4 is not.

**Caveat:** this scores agreement, not correctness. Two quants wrong in the same way
would look identical here. It answers "does more precision change the extraction",
not "is the extraction right".

**Open:** `qat` (16 GB — *smaller* than current, trained to survive quantisation) is
the one variant that could plausibly beat q4 on both quality and cost. Testing it
needs an ollama upgrade, which restarts the embedding provider both brains depend on.
