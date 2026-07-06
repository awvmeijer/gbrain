"""Claude wrapper. All Claude calls in the brain kit go through here.

On Max plan, claude-agent-sdk invokes the Claude CLI subprocess and inherits
the CLI's auth — no API key needed. Swap the internals here if you move off Max.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

# Marker that brain hooks read to skip recording internal agent-SDK calls
# (reflection, deep-sleep summaries, triage, …) as user `claude_session`
# episodes. Subprocesses inherit env, so the marker propagates through
# claude-agent-sdk → Claude Code → SessionEnd hook.
_INTERNAL_ENV = "BRAIN_INTERNAL_AGENT"


def _enter_internal() -> str | None:
    prev = os.environ.get(_INTERNAL_ENV)
    os.environ[_INTERNAL_ENV] = "1"
    return prev


def _exit_internal(prev: str | None) -> None:
    if prev is None:
        os.environ.pop(_INTERNAL_ENV, None)
    else:
        os.environ[_INTERNAL_ENV] = prev


@dataclass
class ClaudeResult:
    text: str
    session_id: str | None


# A logged-out CLI emits this as assistant TEXT (exit 0), not only as an
# error-flagged result — so text presence alone can never prove success.
_AUTH_SENTINEL = "Not logged in"


def _auth_failed(text: str) -> bool:
    return text.strip().startswith(_AUTH_SENTINEL)


async def _collect(
    prompt: str,
    *,
    model: str,
    system: str | None,
    resume: str | None,
) -> ClaudeResult:
    kwargs: dict[str, Any] = {"model": model, "allowed_tools": []}
    if system is not None:
        kwargs["system_prompt"] = system
    if resume is not None:
        kwargs["resume"] = resume
    opts = ClaudeAgentOptions(**kwargs)

    text_parts: list[str] = []
    session_id: str | None = None

    try:
        async for msg in query(prompt=prompt, options=opts):
            name = type(msg).__name__
            if name in ("AssistantMessage", "Assistant"):
                content = getattr(msg, "content", None) or []
                if isinstance(content, str):
                    text_parts.append(content)
                else:
                    for block in content:
                        t = getattr(block, "text", None)
                        if t:
                            text_parts.append(t)
            elif name in ("ResultMessage", "Result"):
                session_id = getattr(msg, "session_id", None) or session_id
    except Exception:
        # The CLI reports API-side failures (429/529 overload) as an
        # error-flagged result AFTER any assistant text has streamed ("Claude
        # Code returned an error result: success"). If real text already
        # arrived, the turn is usable — don't discard it.
        if not text_parts or _auth_failed("".join(text_parts)):
            raise

    text = "".join(text_parts).strip()
    if _auth_failed(text):
        # Never hand the logged-out notice to callers as a "completion".
        raise RuntimeError("claude CLI not logged in — run `claude` then /login")
    return ClaudeResult(text=text, session_id=session_id)


def complete(
    prompt: str,
    *,
    model: str,
    system: str | None = None,
    resume: str | None = None,
) -> ClaudeResult:
    """One-shot Claude call. Pass `resume=session_id` for multi-turn continuity.

    Retries twice with backoff — API-side blips (429/529 overload) surface as
    generic exceptions here, and a bounded retry rides them out the same way
    the ollama wrapper does. A persistent failure (e.g. logged-out CLI) still
    raises after the last attempt.
    """
    prev = _enter_internal()
    try:
        last_err: Exception | None = None
        for delay_s in (0, 2, 8):
            if delay_s:
                time.sleep(delay_s)
            try:
                return asyncio.run(_collect(prompt, model=model, system=system, resume=resume))
            except Exception as e:  # noqa: BLE001 — bounded retry, re-raised below
                last_err = e
        raise last_err  # type: ignore[misc]
    finally:
        _exit_internal(prev)


def ping(model: str, *, timeout_s: float = 60.0) -> dict[str, Any]:
    """Cheap liveness/auth probe for the Claude reasoning path. Never raises.

    Spawns one minimal claude-agent-sdk call and reports whether it
    authenticated. This is the probe that catches the silent 401 when the CLI
    login lapses (e.g. a new-machine migration) — a failure that otherwise
    only surfaces indirectly, days later, as deep-sleep / reflection going
    stale. Marked internal so the probe never lands as a ``claude_session``
    episode.

    Tri-state ``{"ok": bool | None, "error": str | None}``:
    - ``True``  — authenticated and answered.
    - ``False`` — a *fast, consistent* failure (e.g. 401 invalid credentials);
      the SDK subprocess exits quickly. This is the alertable state.
    - ``None``  — the probe timed out. Headless ``claude -p`` cold-start is
      slow (~6-12s, occasionally more under load), so a timeout is treated as
      transient/unknown, NOT down — it must never cry wolf with a false urgent
      "Claude DOWN" push. The next cycle retries.

    Caller MUST be in a sync context (uses ``asyncio.run``) — health.py probes
    only from the source_freshness monitor, never the async request path.
    """
    prev = _enter_internal()
    try:

        async def _probe() -> str:
            res = await asyncio.wait_for(
                _collect("Reply with the single word: ok", model=model, system=None, resume=None),
                timeout=timeout_s,
            )
            return res.text

        text = asyncio.run(_probe())
        if _auth_failed(text):
            return {"ok": False, "error": "claude CLI not logged in — run `claude` then /login"}
        return {"ok": bool(text), "error": None}
    except TimeoutError:
        # Slow/loaded, not broken — unknown, never alertable.
        return {"ok": None, "error": f"probe exceeded {timeout_s:.0f}s (transient)"}
    except Exception as e:  # noqa: BLE001 — probes report, never raise
        return {"ok": False, "error": str(e)[:200]}
    finally:
        _exit_internal(prev)


async def stream(
    prompt: str,
    *,
    model: str,
    system: str | None = None,
    resume: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield stream events as the agent SDK produces them.

    Turns the SDK's ``include_partial_messages`` StreamEvents into small
    dicts the FastAPI endpoint can format as SSE frames without caring about
    SDK internals. Each yield is exactly one of::

        {"type": "delta", "text": "<fragment>"}
        {"type": "done",  "session_id": "<uuid|None>", "model": "<model>"}

    On SDK exception this coroutine raises; the caller translates to an SSE
    ``error`` frame. Caller MUST be in an async context (no ``asyncio.run``).
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "allowed_tools": [],
        "include_partial_messages": True,
    }
    if system is not None:
        kwargs["system_prompt"] = system
    if resume is not None:
        kwargs["resume"] = resume
    opts = ClaudeAgentOptions(**kwargs)

    prev = _enter_internal()
    try:
        session_id: str | None = None
        async for msg in query(prompt=prompt, options=opts):
            name = type(msg).__name__
            if name == "StreamEvent":
                ev = getattr(msg, "event", None) or {}
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            yield {"type": "delta", "text": text}
            elif name in ("ResultMessage", "Result"):
                session_id = getattr(msg, "session_id", None) or session_id

        yield {"type": "done", "session_id": session_id, "model": model}
    finally:
        _exit_internal(prev)
