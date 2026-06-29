# Synthesis A/B — local (qwen3) vs Claude Max bridge

Phase 1, 2026-06-29. Question the user asked: A/B local-vs-Claude synthesis
quality first, then default the deep tier to the winner.

## Setup
Same brain (fresh: 1 nvidia note + 10 Discord #general day-pages, bge-m3 1024d
embeddings). Same `gbrain think` questions, two `--model` backends:
- **Local:** `ollama:qwen3:30b-a3b`
- **Bridge:** `max-bridge:claude-sonnet` (Claude Max via the local CLI bridge)

## Result
| Dimension | qwen3 local | Claude (bridge) |
|---|---|---|
| Factual correctness | ✅ correct | ✅ correct |
| Citations | ✅ (1–7) | ✅ (1–7) |
| Structure | flat paragraph | sectioned (Projects / Interests), scannable |
| Grounded specifics | good | **better** (recovered `feat(fintwit)`, `fintwit_handles.yaml`, Nitter, the Ollama classifier, `analysis-summary-section.tsx` +86) |
| Inference | literal | **better** (meta-interests: local-LLM, AI productivity) |
| Output hygiene | sometimes raw JSON / `LLM_OUTPUT_NOT_JSON` | cleaner, occasional same warning |
| Speed / cost | fast, $0, local | slower (CLI cold start), $0 (flat-fee Max) |

**Verdict:** Claude (bridge) is the better *deep synthesis* engine — noticeably
richer and better-organized. qwen3 is perfectly good for high-volume
extraction/classification where quality-per-token matters less than cost/speed.

## Tiering decision (grounded by this A/B)
- `models.tier.deep` = **max-bridge:claude-sonnet** — synthesis: `think`,
  briefings, reflection (quality-critical, lower volume).
- `models.tier.reasoning` = **ollama:qwen3:30b-a3b** — default chat + fact
  extraction (high volume → keep local & free, mirrors the proven Brains split).
- `models.tier.utility` = **ollama:qwen2.5:7b-instruct-q4_K_M** — fast
  classification / expansion / verdicts.
- `models.tier.subagent` — left at the Anthropic default (tool-loop shape;
  out of scope until a tool-using flow needs it).

## Follow-ups
- **JSON output**: `gbrain think` requests JSON; both models sometimes return
  prose-wrapped JSON → `LLM_OUTPUT_NOT_JSON` + regex citation fallback (works,
  but lossy). Phase 2: have the bridge honor `response_format`/strip `<think>`,
  or relax think's parser. qwen3 hits this harder than Claude.
- **Free-X intel**: the synthesis recovered that the user's FinTwit digest
  already pulls 55 handles via **Nitter** + a local Ollama classifier — reuse
  that pipeline for the P3 free-X ingestion recipe (no paid X API).
