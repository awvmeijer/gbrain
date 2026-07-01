# Local-LLM A/B — reasoning/extraction tier (2026-07-01)

Deep synthesis stays on **Claude** (`max-bridge:claude-sonnet`). This A/B is only
the local **reasoning/extraction tier** (`models.tier.reasoning`), whose job is
terse, high-volume structured extraction (feed entity/ticker pull, enrichment,
query expansion) — not deep reasoning.

## Method
`docs/brains-port/local_llm_ab.py` — 12 real feed pages (x / telegram / youtube)
run through each model on the ticker-extraction task the tier actually does
(ollama `/api/chat`, `format=json`, `think=false`, `temperature=0`). Metrics:
JSON-parse success rate, throughput (tokens/sec), latency. Each model warmed once
before timing.

## Result

| model | JSON ok | tickers found | tok/s | avg latency |
|---|---|---|---|---|
| qwen3:30b-a3b (incumbent) | 83.3% | 79 | 72.4 | 4.0s |
| **gemma4:26b (winner)** | **100%** | **102** | 70.7 | 3.7s |

Gemma 4 wins on the metric that matters most for this role — **100% clean JSON**
(eliminates the `LLM_OUTPUT_NOT_JSON` regex fallback) vs qwen3's 83% — with
**better recall** (102 vs 79 tickers) at essentially equal speed. Gemma emits no
`<think>` block, so structured output is clean by default. Nemotron was excluded
up front (reasoning-heavy + large; the deep-reasoning role is already Claude's).

## Decision
```
gbrain config set models.tier.reasoning ollama:gemma4:26b
```
Config-string flip only (invariant: no hardcoded model IDs). Deep tier (Claude)
and utility tier (`qwen2.5:7b`) unchanged. Reversible:
`gbrain config set models.tier.reasoning ollama:qwen3:30b-a3b`.

Note: the tier config lives in the brain DB (set via `gbrain config set`), so it
is recorded here for reproducibility rather than in a committed config file.
