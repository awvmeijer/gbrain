"""Claude Max CLI bridge — OpenAI-compatible shim over `claude-agent-sdk`.

GBrain calls LLMs over HTTP via the Vercel AI SDK; the `claude` CLI is a
subprocess, not an HTTP API. This tiny server exposes an OpenAI-compatible
surface (`/v1/chat/completions`, `/v1/models`, `/health`) backed by the SDK,
so GBrain's `max-bridge` recipe (an `openai-compatible` recipe pointed here)
gets **flat-fee Claude Max** reasoning instead of a metered Anthropic key.

It vendors the claude-agent-sdk wrapper as `_claude_sdk.py` (`complete`/
`stream`/`ping`; Max-plan auth inherited from the local `claude` CLI). No
dependency on the old ~/brain repo.

Deliberately:
- **No `/v1/embeddings`.** Embeddings go straight to Ollama (bge-m3). The Max
  plan must never be billed/throttled for embeds, and Claude has no embed API.
- **Text-only.** The SDK is called with `allowed_tools=[]`; this bridge is for
  deep *synthesis*, not tool-calling/subagent loops. GBrain's `max-bridge`
  recipe declares `supports_tools: false` so the gateway never sends tools here.

Run (self-contained sidecar venv):
    sidecars/.venv/bin/python sidecars/max-bridge/server.py
Env:
    MAXBRIDGE_PORT   (default 8789)
    MAXBRIDGE_MAX_CONCURRENCY (default 2) — cold-start is slow; cap parallelism
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

# Vendored claude-agent-sdk wrapper (self-contained — no ~/brain dependency).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _claude_sdk as claude  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

app = FastAPI(title="claude-max-bridge")

# GBrain sends the modelId (the part after `max-bridge:`). Map friendly tier
# names to the dated Claude ids the CLI/SDK expects; pass unknown ids through.
_MODEL_MAP = {
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-opus": "claude-opus-4-8",
    "claude-haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "haiku": "claude-haiku-4-5",
}
_DEFAULT_MODEL = "claude-sonnet-4-6"
_PROBE_MODEL = _MODEL_MAP["claude-sonnet"]

# Cold-start of `claude -p` is ~6–12s; cap concurrent CLI subprocesses so a
# burst of GBrain calls doesn't spawn an unbounded fan of `claude` processes.
_SEM = asyncio.Semaphore(int(os.environ.get("MAXBRIDGE_MAX_CONCURRENCY", "2")))


def _resolve_model(m: str | None) -> str:
    if not m:
        return _DEFAULT_MODEL
    return _MODEL_MAP.get(m, m)


def _text_of(content) -> str:
    """OpenAI content may be a string or a list of {type,text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in (None, "text")
        )
    return ""


def _split_messages(messages: list[dict]) -> tuple[str, str | None]:
    """Hoist system messages → system_prompt; fold the rest into one prompt."""
    system_parts: list[str] = []
    convo: list[tuple[str, str]] = []
    for msg in messages or []:
        role = msg.get("role")
        text = _text_of(msg.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
        else:
            convo.append((role or "user", text))
    system = "\n\n".join(system_parts) or None
    if len(convo) <= 1:
        prompt = convo[-1][1] if convo else ""
    else:
        # Multi-turn: prefix non-user turns so the model sees the structure.
        prompt = "\n\n".join((t if r == "user" else f"[{r}]\n{t}") for r, t in convo)
    return prompt, system


# --- JSON-mode enforcement -------------------------------------------------
# GBrain's `think` (and other structured callers) ask the model for a JSON
# object in the prompt, then JSON.parse the reply. Claude via the CLI often
# returns prose instead → LLM_OUTPUT_NOT_JSON + regex citation fallback. When
# a request clearly wants JSON, nudge hard and unwrap the reply so GBrain gets
# clean JSON. Detection is conservative (only fires on explicit JSON asks).
_JSON_DIRECTIVE = (
    "CRITICAL OUTPUT FORMAT: Respond with ONLY a single valid JSON object "
    "matching the requested schema. No markdown code fences, no prose before "
    "or after the JSON."
)
_FENCE_HEAD = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)


def _wants_json(prompt: str, system: str | None) -> bool:
    blob = ((system or "") + "\n" + (prompt or "")).lower()
    if "json" not in blob:
        return False
    return any(
        k in blob
        for k in ('"answer"', '"citations"', '"gaps"', "json object", "valid json",
                  "respond with json", "return json", "as json", "json schema")
    )


def _rf_is_json(rf) -> bool:
    return isinstance(rf, dict) and str(rf.get("type", "")).startswith("json")


def _extract_json(text: str) -> str:
    """Best-effort: return clean JSON from a reply that may be fenced or
    prose-wrapped. Falls back to the raw text if no object is found."""
    s = text.strip()
    if s.startswith("```"):
        s = _FENCE_HEAD.sub("", s).strip()
        if s.endswith("```"):
            s = s[: s.rfind("```")].strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        return s[i : j + 1]
    return text


@app.get("/health")
async def health():
    res = await run_in_threadpool(claude.ping, _PROBE_MODEL)
    ok = res.get("ok")
    # ok is True | False | None (None = transient/slow). Only False is "down".
    status = 503 if ok is False else 200
    return JSONResponse({"ok": ok, "error": res.get("error")}, status_code=status)


@app.get("/v1/models")
async def models():
    now = int(time.time())
    data = [
        {"id": mid, "object": "model", "created": now, "owned_by": "claude-max-cli"}
        for mid in ("claude-sonnet", "claude-opus", "claude-haiku")
    ]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    body = await req.json()
    sent_model = body.get("model")
    model = _resolve_model(sent_model)
    prompt, system = _split_messages(body.get("messages") or [])
    want_json = _wants_json(prompt, system) or _rf_is_json(body.get("response_format"))
    if want_json:
        system = (system + "\n\n" if system else "") + _JSON_DIRECTIVE
    stream = bool(body.get("stream"))
    created = int(time.time())
    cid = f"chatcmpl-maxbridge-{created}"

    if not stream:
        async with _SEM:
            result = await run_in_threadpool(
                lambda: claude.complete(prompt, model=model, system=system)
            )
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": sent_model or model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": _extract_json(result.text) if want_json else result.text,
                    },
                    "finish_reason": "stop",
                }
            ],
            # The SDK gives no real token counts; report zeros (best-effort).
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    async def event_stream():
        # Opening chunk carries the assistant role (OpenAI convention).
        first = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": sent_model or model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first)}\n\n"
        try:
            async with _SEM:
                async for ev in claude.stream(prompt, model=model, system=system):
                    if ev.get("type") == "delta" and ev.get("text"):
                        chunk = {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": sent_model or model,
                            "choices": [
                                {"index": 0, "delta": {"content": ev["text"]}, "finish_reason": None}
                            ],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as e:  # noqa: BLE001 — surface as an OpenAI-shaped error frame
            yield f"data: {json.dumps({'error': {'message': str(e)[:300], 'type': 'max_bridge_error'}})}\n\n"
        final = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": sent_model or model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MAXBRIDGE_PORT", "8789"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
