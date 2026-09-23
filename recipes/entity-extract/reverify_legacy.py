#!/usr/bin/env python3
"""Re-check the pre-gate `llm-qwen3` edges against the brain's own text.

Those 1,063 edges were written before the quote gate and without provenance —
no source page, no context. For each one: find chunks that name both entities
(read-only query), split them into sentences, and run the same quote gate the
live pass uses (llm_relations.quote_gate). An edge with a passing sentence is
re-written as `llm-qwen3-quoted` with that sentence and its page as context; an
edge with none is removed. Every removal is appended to
logs/llm-relations-legacy-removed.jsonl first (restore = `gbrain link` each row).

Default is a dry run (counts + samples, no writes). `--apply` changes the graph
through the sanctioned `gbrain link` / `gbrain unlink` ops only.

    python reverify_legacy.py [--limit N] [--apply]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
GBRAIN = Path.home() / "gbrain"
REMOVED_LOG = GBRAIN / "logs" / "llm-relations-legacy-removed.jsonl"
PLAN = GBRAIN / "logs" / "llm-relations-reverify-plan.jsonl"
LEGACY = "llm-qwen3"

_spec = importlib.util.spec_from_file_location("llm_relations", HERE / "llm_relations.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)  # the same gate, prompt-free: quote_gate, _norm, _named_in


def psql_json(sql: str, params: dict[str, str] | None = None):
    url = json.loads((Path.home() / ".gbrain" / "config.json").read_text())["database_url"]
    args = ["psql", url, "-At", "-v", "ON_ERROR_STOP=1"]
    for k, v in (params or {}).items():
        args += ["-v", f"{k}={v}"]
    r = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PGOPTIONS": "-c default_transaction_read_only=on"})
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout or "null")


def gb(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout if r.returncode == 0 else (r.stdout or "") + (r.stderr or ""))


def display_name(slug: str, title: str | None) -> str:
    if slug.startswith("ticker/"):
        return slug.split("/", 1)[1].upper()
    return (title or slug.split("/", 1)[-1].replace("-", " ")).strip()


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p and p.strip()]


def find_receipt(s_name: str, o_name: str, pred: str) -> tuple[str, str] | None:
    """(page slug, sentence) for the first sentence that passes the gate, else None."""
    def pat(name: str) -> str:
        return "%" + name.lstrip("$").replace("%", "").replace("_", "\\_") + "%"
    rows = psql_json(
        """SELECT coalesce(json_agg(t), '[]'::json) FROM (
             SELECT p.slug, c.chunk_text FROM content_chunks c JOIN pages p ON p.id = c.page_id
              WHERE p.deleted_at IS NULL AND p.slug NOT LIKE 'entities/%%' AND p.slug NOT LIKE 'ticker/%%'
                AND p.slug NOT LIKE 'digests/%%' AND p.slug NOT LIKE 'proposals/%%'  -- summaries are not sources
                AND c.chunk_text ILIKE :'a' AND c.chunk_text ILIKE :'b'
              LIMIT 25) t""".replace("%%", "%"),
        {"a": pat(s_name), "b": pat(o_name)},
    )
    for row in rows or []:
        page_n = lr._norm(row["chunk_text"])
        for sent in sentences(row["chunk_text"]):
            if not (lr.QUOTE_MIN <= len(sent) <= lr.QUOTE_MAX):
                continue
            quote, verdict = lr.quote_gate({"quote": sent}, s_name, o_name, pred, page_n)
            if quote:
                return row["slug"], quote
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="rewrite / remove edges (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N edges (0 = all)")
    ap.add_argument("--remove-all", action="store_true",
                    help="skip the re-check and remove every legacy edge (restore file first)")
    args = ap.parse_args()

    edges = psql_json(
        """SELECT coalesce(json_agg(t ORDER BY t.from_slug, t.to_slug), '[]'::json) FROM (
             SELECT pf.slug AS from_slug, pf.title AS from_title, pt.slug AS to_slug, pt.title AS to_title,
                    l.link_type
               FROM links l JOIN pages pf ON pf.id = l.from_page_id JOIN pages pt ON pt.id = l.to_page_id
              WHERE l.link_source = 'llm-qwen3') t"""
    )
    if args.limit:
        edges = edges[: args.limit]
    print(f"reverify: {len(edges)} legacy {LEGACY} edges ({'APPLY' if args.apply else 'dry run'})", flush=True)

    kept, removed, failed = [], [], 0
    if args.remove_all:
        # 2026-09-23 decision: a re-check cannot reach 90% precision (hand-checked
        # sample: gate 28%, local judge 45%, Claude judge ~56%), so none are kept.
        removed = [(e, None) for e in edges]
        edges = []
    for i, e in enumerate(edges, 1):
        s_name, o_name = display_name(e["from_slug"], e["from_title"]), display_name(e["to_slug"], e["to_title"])
        try:
            hit = find_receipt(s_name, o_name, e["link_type"])
        except Exception as ex:  # noqa: BLE001 — one bad query must not stop the sweep
            failed += 1
            print(f"  ERR {e['from_slug']} --{e['link_type']}--> {e['to_slug']}: {ex}", file=sys.stderr)
            continue
        (kept if hit else removed).append((e, hit))
        if i % 100 == 0:
            print(f"  … {i}/{len(edges)}: {len(kept)} verified, {len(removed)} without a receipt", flush=True)

    print(f"reverify: {len(kept)} verified · {len(removed)} without a receipt · {failed} errors")
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    with PLAN.open("w") as fh:  # the full plan, for review before --apply
        for e, hit in kept + removed:
            fh.write(json.dumps({**e, "verdict": "keep" if hit else "remove",
                                 "source": hit[0] if hit else None, "quote": hit[1] if hit else None}) + "\n")
    print(f"plan: {PLAN}")
    for e, (slug, quote) in kept[:8]:
        print(f"  KEEP {e['from_slug']} --{e['link_type']}--> {e['to_slug']} | {quote[:100]!r} ({slug})")
    for e, _ in removed[:8]:
        print(f"  DROP {e['from_slug']} --{e['link_type']}--> {e['to_slug']}")
    if not args.apply:
        return

    REMOVED_LOG.parent.mkdir(parents=True, exist_ok=True)
    done_kept = done_removed = 0
    for e, (slug, quote) in kept:
        ctx = f"“{quote[:300]}” — {slug}"
        rc, out = gb(["link", e["from_slug"], e["to_slug"], "--link-type", e["link_type"],
                      "--link-source", lr.LINK_SOURCE, "--context", ctx])
        if rc != 0:
            print(f"  link FAILED {e['from_slug']} -> {e['to_slug']}: {out.strip()[:120]}", file=sys.stderr)
            continue  # keep the legacy edge rather than lose it
        rc, out = gb(["unlink", e["from_slug"], e["to_slug"], "--link-type", e["link_type"], "--link-source", LEGACY])
        done_kept += rc == 0
    with REMOVED_LOG.open("a") as log:
        for e, _ in removed:
            log.write(json.dumps({**e, "link_source": LEGACY}) + "\n")  # written BEFORE the unlink
            log.flush()
            rc, out = gb(["unlink", e["from_slug"], e["to_slug"], "--link-type", e["link_type"], "--link-source", LEGACY])
            if rc == 0:
                done_removed += 1
            else:
                print(f"  unlink FAILED {e['from_slug']} -> {e['to_slug']}: {out.strip()[:120]}", file=sys.stderr)

    # Let the gated pass re-find removed relations later, now with a receipt.
    try:
        st = json.loads(lr.STATE.read_text())
        drop = {(e["from_slug"], e["to_slug"], e["link_type"]) for e, _ in removed}
        st["edges"] = [x for x in st.get("edges", []) if tuple(x) not in drop]
        lr.STATE.write_text(json.dumps(st))
    except Exception as ex:  # noqa: BLE001
        print(f"  state update skipped: {ex}", file=sys.stderr)
    print(f"reverify APPLIED: {done_kept} re-written with a receipt, {done_removed} removed "
          f"(restore list: {REMOVED_LOG})")


if __name__ == "__main__":
    main()
