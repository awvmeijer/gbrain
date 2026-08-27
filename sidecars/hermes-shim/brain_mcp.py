#!/usr/bin/env python3
"""Hermes ⇄ brain shim — a stdio MCP server over the capture sidecar's JSON API.

Why this exists (docs/brains-port/forge-hermes-bridge.md, platform-contract.md):
`gbrain serve` holds the PGLite write lock for the whole session, which starves
the capture sidecar. Until the Postgres migration lands, external agents reach
the brain through this shim instead — it translates MCP tool calls into HTTP
against 127.0.0.1:8787, so the sidecar remains the sole gbrain invoker.

Safety contract (four layers; this is layer 1): the toolset is read + capture +
PROPOSE only. There is deliberately no decide, no raw page put, no send.
A human approves proposals on the dashboard or the Telegram approvals bot.

Run (Hermes spawns it via mcp_servers config):
    ~/gbrain/sidecars/.venv/bin/python ~/gbrain/sidecars/hermes-shim/brain_mcp.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("BRAIN_API_URL", "http://127.0.0.1:8787")
KEY_FILE = Path.home() / ".gbrain" / "client.key"
AGENT = os.environ.get("BRAIN_AGENT_NAME", "hermes")

mcp = FastMCP("brain")


def _key() -> str:
    k = os.environ.get("BRAIN_API_KEY") or (
        KEY_FILE.read_text().strip() if KEY_FILE.exists() else ""
    )
    if not k:
        raise RuntimeError(f"no brain key: set BRAIN_API_KEY or create {KEY_FILE}")
    return k


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, headers={"X-Brain-Key": _key()}, timeout=30.0)


@mcp.tool()
def brain_search(query: str, limit: int = 8) -> str:
    """Hybrid semantic + keyword search over the brain (episodic memory: notes,
    sessions, feeds, tickers, emails, transcripts). Returns scored hits with
    page slugs — cite slugs in answers so the human can open them."""
    with _client() as c:
        r = c.get("/api/search", params={"q": query, "n": max(1, min(limit, 25))})
        r.raise_for_status()
        hits = r.json().get("hits", [])
    if not hits:
        return "No hits."
    return "\n".join(f"[{h['score']:.2f}] {h['slug']} — {h.get('snippet', '').strip()}"
                     for h in hits)


@mcp.tool()
def brain_ask(question: str, fast: bool = False) -> str:
    """Deep, cited multi-hop answer synthesized by the brain's own reasoning
    layer (Claude via the local Max bridge). Use for anything needing synthesis
    across many memories; use brain_search for quick lookups. fast=True skips
    the LLM and returns raw retrieval."""
    # /api/ask streams SSE (start → token* → done{grounding}); collapse it.
    text, grounding = [], []
    with _client() as c, c.stream("POST", "/api/ask", json={"q": question, "fast": fast},
                                  timeout=300.0) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            evt = json.loads(line[6:])
            if evt.get("event") == "token":
                text.append(evt.get("text", ""))
            elif evt.get("event") == "done":
                grounding = evt.get("grounding", [])
            elif evt.get("event") == "error":
                return f"brain_ask failed: {evt.get('detail', 'unknown error')}"
    answer = "".join(text).strip() or "(no answer)"
    if grounding:
        answer += "\n\nGrounding: " + ", ".join(grounding)
    return answer


@mcp.tool()
def brain_capture(text: str) -> str:
    """Save a salient fact, decision, preference, or insight to the brain's
    permanent memory. Use whenever the human tells you something worth
    remembering across sessions — the brain, not your session log, is the
    canonical memory."""
    with _client() as c:
        r = c.post("/capture", json={"text": text, "source": AGENT})
        r.raise_for_status()
        data = r.json()
    if data.get("duplicate"):
        return "Already captured recently (duplicate skipped)."
    return f"Captured → {data.get('page')}"


@mcp.tool()
def brain_needs() -> str:
    """List pending proposals awaiting the human's approve/reject decision
    (read-only — deciding happens on the dashboard or Telegram, never here)."""
    with _client() as c:
        r = c.get("/api/needs")
        r.raise_for_status()
        props = r.json().get("proposals", [])
    if not props:
        return "Nothing pending."
    return "\n\n".join(
        f"• {p['title']} ({p['slug']})\n  action: {p['action']} → {p['target']}\n"
        f"  why: {p['rationale']}" for p in props)


@mcp.tool()
def brain_propose(title: str, action: str, target: str, rationale: str,
                  rollback: str, body: str = "") -> str:
    """Propose a side-effectful action (send an email/message, place an order,
    change something outside this chat) for HUMAN approval. You must never
    perform such actions yourself — propose, and a human decides. All fields
    required: action (machine verb), target (what it acts on), rationale (why,
    human-readable), rollback (how to undo). Put draft content in body."""
    payload = {"title": title, "action": action, "target": target,
               "rationale": rationale, "rollback": rollback, "body": body,
               "proposed_by": AGENT}
    with _client() as c:
        r = c.post("/api/propose", json=payload)
        r.raise_for_status()
        data = r.json()
    return (f"Proposed → {data['slug']} (status: pending). "
            "A human will approve or reject it; do not act on it yourself.")


if __name__ == "__main__":
    mcp.run()  # stdio transport
