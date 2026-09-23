#!/usr/bin/env python3
"""LLM typed entity relations (BRAINS Phase 3 — KG parity with the old brain).

The old ~/brain ran qwen open-schema entity/relation extraction on every
episode. gbrain's dream cycle covers facts / salience / consolidation /
symbol edges, and recipes/entity-extract/backfill.py covers deterministic
tier-0 entities (cashtags, filings, papers) — the remaining gap is TYPED
relations between named entities ("NVDA supplies OpenAI", "Ana works_at NTU").

This recipe fills it locally and for $0: qwen3 via Ollama over RECENT ingest
pages, writing edges through the sanctioned `gbrain link` op — the exact
pattern of backfill.py (pre-create entity pages, then explicit typed edges;
gbrain's NER only handles hardcoded kinds and drops links to missing pages).

Scheduling: infra/dream-cron.sh runs this nightly AFTER `gbrain dream`
(03:30, com.brains.dream.plist). Short-lived `gbrain` invocations only, so
it is safe on PGLite while the dream itself stays gated on Postgres.

Quote gate (2026-09-23): the model must return, per relation, the verbatim
sentence that states it; an edge is written only if code confirms the quote is
in the page, names both entities, grounds the predicate's verb, and — for role
predicates (ceo_of, works_at, …) — has the subject next to the role word. Kept
edges are tagged `llm-qwen3-quoted` and carry "<quote>" — <source slug> as their
link context. The 1,063 earlier `llm-qwen3` edges predate the gate and have no
receipt. `--only-prefix` restricts a run (e.g. youtube/) for targeted checks.

Idempotent: logs/llm-relations-state.json records processed pages, created
entity pages, and (src, dst, type) edges — re-runs are no-ops. Entity slugs
join the existing graph where possible: a subject/object that is a known
ticker symbol (from backfill.py's state) lands on ticker/<sym>, everything
else under entities/<slug>.

Usage:
    python llm_relations.py [--days 2] [--limit 40] [--dry-run]
                            [--model qwen3:30b-a3b] [--ollama http://127.0.0.1:11434]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
GBRAIN = HOME / "gbrain"
ING = HOME / "brains-ingest"
STATE = GBRAIN / "logs" / "llm-relations-state.json"
TIER0_STATE = GBRAIN / "logs" / "entity-extract-state.json"  # backfill.py's created pages
LOG = GBRAIN / "logs" / "llm-relations.log"

MAX_BODY_CHARS = 4000
MAX_RELATIONS_PER_PAGE = 8

# Skip pages that are synthesized or process artifacts, not source material:
# digests summarize other pages (self-referential edges), proposals are
# workflow state, entity/filing hubs are graph nodes themselves.
# capture/ (2026-09-23): forge2 insight captures arrive ~1,400/day and carry no
# entity-to-entity relations. Newest-first, they filled all 200 nightly slots —
# 0 new edges every night from 2026-08-16 until this skip.
SKIP_PREFIXES = ("digests/", "proposals/", "ticker/", "filings/", "papers/", "entities/",
                 "capture/", "sessions/", "git/")
# sessions/ and git/ (2026-09-23): session records and commits are metadata
# (title, files, commit subjects) — 0 relations proposed across a sampled run.

# Entities the model loves to emit that are never useful graph nodes.
ENTITY_STOP = {
    "i", "me", "you", "he", "she", "it", "we", "they", "user", "author", "speaker",
    "the company", "the market", "everyone", "people", "investors", "traders",
    "this", "that", "today", "tomorrow", "the fed",
}

SYSTEM_PROMPT = """You extract typed relations between NAMED entities from a note.

Rules:
- Only entities explicitly named in the text: people, companies, tickers, organizations, products, technologies, places.
- Only relations the text actually states or strongly implies — never invent.
- predicate: a short lowercase snake_case verb phrase, e.g. acquired, partners_with, ceo_of, works_at, supplies, invested_in, competes_with, subsidiary_of, launched, sued_by, holds_position_in.
- At most {max_rel} relations. Fewer is better than doubtful.
- If the note contains no clear entity-to-entity relations, return an empty list.

- quote: the exact sentence from the note that states the relation, copied verbatim (no paraphrase,
  no ellipsis, at most 300 characters). It must name both the subject and the object. No quote → no relation.

Answer with JSON ONLY, exactly this shape:
{{"relations": [{{"subject": "NVDA", "predicate": "supplies", "object": "OpenAI",
  "quote": "Nvidia supplies OpenAI with GB200 racks under the new deal."}}]}}"""


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[llm-relations] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def gb(args: list[str], stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(["gbrain", *args], cwd=str(GBRAIN), input=stdin,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode == 0:
        return 0, r.stdout or ""  # stderr carries gbrain's UPGRADE_AVAILABLE notice
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"pages": [], "entity_pages": [], "edges": []}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def known_tickers() -> set[str]:
    """Ticker symbols whose ticker/<sym> page already exists (backfill.py state)."""
    try:
        pages = json.loads(TIER0_STATE.read_text()).get("pages", [])
    except Exception:
        return set()
    return {p.split("/", 1)[1] for p in pages if p.startswith("ticker/")}


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def norm_predicate(p: str) -> str:
    p = re.sub(r"[^a-z0-9]+", "_", (p or "").lower()).strip("_")
    return p[:40]


def entity_slug(name: str, tickers: set[str]) -> tuple[str, str] | None:
    """(page slug, display name) for an extracted entity, or None if junk."""
    name = re.sub(r"\s+", " ", (name or "")).strip().strip("$")
    if not (2 <= len(name) <= 64) or name.lower() in ENTITY_STOP:
        return None
    # A bare 1-5 cap symbol that already has a ticker hub joins the existing graph.
    if re.fullmatch(r"[A-Z]{1,5}", name) and name.lower() in tickers:
        return f"ticker/{name.lower()}", name
    slug = slugify(name)
    if len(slug) < 2:
        return None
    return f"entities/{slug}", name


# ---------------------------------------------------------------- quote gate
#
# 2026-09-23: without a check the pass wrote "Nvidia owns NBIS", "Nvidia ceo_of
# ServiceNow", "Nvidia acquires IREN" — 1,063 edges, none with a receipt. Now the
# model must return the sentence that states the relation, and an edge is written
# only if code confirms: (1) the quote is verbatim in the page, (2) both entities
# are named in it, (3) the predicate's verb is grounded in it. Precision over
# recall: a doubtful edge is dropped, and every kept edge carries its quote.

LINK_SOURCE = "llm-qwen3-quoted"
QUOTE_MIN, QUOTE_MAX = 12, 400

# Predicate → stems, any one of which must appear in the quote. Unlisted
# predicates fall back to the stems of their own words (see _predicate_stems).
PREDICATE_STEMS = {
    "owns": ("own", "stake", "holds", "holding"),
    "acquires": ("acqui", "buy", "bought", "purchas", "takeover", "merg"),
    "acquired": ("acqui", "buy", "bought", "purchas", "takeover", "merg"),
    "invested_in": ("invest", "stake", "backed", "fund", "financ"),
    "invests_in": ("invest", "stake", "backed", "fund", "financ"),
    "backed_by": ("back", "invest", "fund", "financ"),
    "supplies": ("suppl", "provid", "deliver", "sell", "sold", "ship", "vendor"),
    "competes_with": ("compet", "rival", " vs", "versus", "against"),
    "partners_with": ("partner", "collaborat", "alliance", "joint", "team"),
    "ceo_of": ("ceo", "chief executive"),
    "works_at": ("work", "join", "employ", "hired", "engineer at", "at "),
    "subsidiary_of": ("subsidiar", "owned by", "unit of", "division", "part of"),
    "launched": ("launch", "releas", "unveil", "introduc", "announc"),
    "sued_by": ("sue", "lawsuit", "litigat", "court"),
    "holds_position_in": ("position", "holds", "long", "short", "shares", "own"),
}
_PRED_STOP = {"with", "of", "in", "by", "to", "on", "for", "at", "from", "the", "a", "an", "is", "has", "be"}


def _norm(text: str) -> str:
    """Lowercase, drop markdown emphasis and quote glyphs, collapse whitespace."""
    text = (text or "").lower()
    text = re.sub(r"[*_`>#|\[\]]", " ", text)
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip()


def _predicate_stems(pred: str) -> tuple[str, ...]:
    if pred in PREDICATE_STEMS:
        return PREDICATE_STEMS[pred]
    words = [w for w in pred.split("_") if w and w not in _PRED_STOP]
    return tuple(w[:5] if len(w) > 5 else w for w in words) or (pred,)


def _named_in(name: str, quote_n: str) -> bool:
    """Is the entity named in the (normalized) quote? Whole name, or — for
    multi-word names — every significant word. `$NVDA` and `NVDA` both count."""
    n = _norm(name).lstrip("$")
    if not n:
        return False
    if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", quote_n):
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", n) if len(w) >= 3]
    return len(words) >= 2 and all(re.search(rf"(?<![a-z0-9]){re.escape(w)}", quote_n) for w in words)


# Speech, sentiment and document verbs describe a statement, not a relation
# between two entities ("Alex Karp says Dario Amodei", "HOOD bullish crypto").
NON_RELATION_PREDICATES = {
    "says", "said", "states", "stated", "mentions", "mentioned", "discusses", "discussed", "notes",
    "noted", "thinks", "believes", "bullish", "bearish", "excited", "comments_on", "talks_about",
    "tweets", "tweeted", "cites", "cited", "highlights", "praises", "criticizes", "whitepaper",
    "compares", "compares_to", "compares_performance_to", "likes", "loves", "hates", "asks",
}


def _is_entity_name(name: str) -> bool:
    """Numbers and amounts are not entities ("1.1 trillion", "41", "$25B")."""
    n = re.sub(r"[-_]+", " ", (name or "").lower()).strip()
    if not re.search(r"[a-z]", n):
        return False
    return not re.fullmatch(r"\$?[\d.,\s]+(%|x|[kmb]n?|bps|thousand|million|billion|trillion)?", n)


def _stem_in(stem: str, quote_n: str) -> bool:
    """Stem at a word start: "ship" must not match inside "partnership"."""
    return re.search(rf"(?<![a-z0-9]){re.escape(stem.strip())}", quote_n) is not None


def quote_gate(rel: dict, s_name: str, o_name: str, pred: str, page_n: str) -> tuple[str | None, str]:
    """(quote, "ok") if the relation is grounded in the page, else (None, reason)."""
    if pred in NON_RELATION_PREDICATES:
        return None, "not_a_relation"
    if not (_is_entity_name(s_name) and _is_entity_name(o_name)):
        return None, "not_an_entity"
    quote = re.sub(r"\s+", " ", str(rel.get("quote") or "")).strip().strip('"\'')
    if not (QUOTE_MIN <= len(quote) <= QUOTE_MAX):
        return None, "no_quote" if not quote else "quote_length"
    quote_n = _norm(quote).rstrip(".")
    if quote_n not in page_n:
        return None, "quote_not_in_page"
    if not (_named_in(s_name, quote_n) and _named_in(o_name, quote_n)):
        return None, "entity_not_in_quote"
    stems = [st for st in _predicate_stems(pred) if _stem_in(st, quote_n)]
    if not stems:
        return None, "predicate_not_in_quote"
    # Role predicates name a PERSON's role: the subject must sit right next to the
    # role word ("Bill McDermott, CEO of ServiceNow" must not become Nvidia ceo_of
    # ServiceNow just because Nvidia appears later in the sentence).
    if pred in ROLE_PREDICATES and not _near(s_name, stems, quote_n, ROLE_WINDOW):
        return None, "role_subject_not_adjacent"
    return quote, "ok"


ROLE_PREDICATES = {"ceo_of", "cto_of", "cfo_of", "chairman_of", "founder_of", "founded", "works_at", "president_of"}
ROLE_WINDOW = 24  # max characters BETWEEN the subject's mention and the role word


def _near(name: str, stems: list[str], quote_n: str, window: int) -> bool:
    """Is some mention of `name` within `window` characters (gap, not offset) of a
    role word? "Bill McDermott, CEO of" → gap 2; "CEO of ServiceNow, Bill McDermott" → 19."""
    n = _norm(name).lstrip("$")
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(n), quote_n)] if n else []
    for st in stems:
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(st.strip())}", quote_n):
            for a, b in spans:
                gap = m.start() - b if m.start() >= b else a - m.end()
                if 0 <= gap <= window or (a <= m.start() < b):
                    return True
    return False


def ollama_chat(base: str, model: str, system: str, user: str, timeout: int = 300) -> str:
    payload = {
        "model": model,
        "stream": False,
        "think": False,  # qwen3: skip the <think> block; we want JSON, not reasoning prose
        "format": "json",
        "options": {"temperature": 0.1},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(f"{base}/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "brains-llm-relations/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("message", {}).get("content", "")


def parse_relations(raw: str) -> list[dict]:
    """format=json should give clean JSON, but stay fence/think-tolerant anyway."""
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    rels = data.get("relations") if isinstance(data, dict) else data
    return [r for r in (rels or []) if isinstance(r, dict)][:MAX_RELATIONS_PER_PAGE]


def page_text(path: Path) -> tuple[str, str]:
    """(title, body-without-frontmatter), body capped for the prompt."""
    text = path.read_text(errors="ignore")
    title = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm, body = text[:end], text[end + 4:]
            m = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
            if m:
                title = m.group(1).strip().strip("'\"")
    body = body.strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "…[truncated]"
    return title, body


ENTITY_PAGE_TMPL = ("---\ntype: entity\ntitle: {name}\nsource: entity-extract\n"
                    "tags: [entity, llm-relations]\n---\n\n# {name}\n\n"
                    "Entity hub (LLM-extracted). Typed relations link here.\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=2, help="only pages modified in the last N days")
    ap.add_argument("--limit", type=int, default=40, help="max pages per run (bounds nightly runtime)")
    ap.add_argument("--model", default="qwen3:30b-a3b")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--dry-run", action="store_true", help="extract + print, write nothing")
    ap.add_argument("--only-prefix", default="", help="restrict to ingest pages under this prefix, e.g. youtube/")
    args = ap.parse_args()

    # Ollama liveness first — a dead daemon should be one log line, not 40 timeouts.
    try:
        urllib.request.urlopen(f"{args.ollama}/api/tags", timeout=5).read()
    except Exception as e:  # noqa: BLE001
        log(f"ollama not reachable at {args.ollama} ({type(e).__name__}) — skipping run")
        return

    st = load_state()
    done_pages = set(st.get("pages", []))
    ent_pages = set(st.get("entity_pages", []))
    edges = {tuple(e) for e in st.get("edges", [])}
    tickers = known_tickers()

    cutoff = time.time() - args.days * 86400
    candidates = []
    for f in sorted(ING.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        rel = f.relative_to(ING).as_posix()
        slug = rel[:-3] if rel.endswith(".md") else rel
        if slug in done_pages or rel.startswith(SKIP_PREFIXES):
            continue
        if args.only_prefix and not rel.startswith(args.only_prefix):
            continue
        if f.stat().st_mtime < cutoff:
            break  # sorted newest-first; everything after is older
        candidates.append((slug, f))
        if len(candidates) >= args.limit:
            break

    log(f"model={args.model} days={args.days} → {len(candidates)} pages to process "
        f"(prior: {len(done_pages)} pages, {len(edges)} edges)")

    system = SYSTEM_PROMPT.format(max_rel=MAX_RELATIONS_PER_PAGE)
    new_edges = 0
    verdicts: dict[str, int] = {}
    for slug, f in candidates:
        title, body = page_text(f)
        page_n = _norm(f.read_text(errors="ignore"))  # the gate checks quotes against the whole page
        if len(body) < 80:  # ticker lists / stubs: nothing relational in there
            done_pages.add(slug)
            continue
        try:
            raw = ollama_chat(args.ollama, args.model, system, f"Title: {title}\n\n{body}")
        except Exception as e:  # noqa: BLE001
            log(f"  {slug}: ollama call failed ({type(e).__name__}) — will retry next run")
            continue
        rels = parse_relations(raw)
        if args.dry_run:
            log(f"  {slug}: {len(rels)} proposed")
        kept = []
        for r in rels:
            pred = norm_predicate(str(r.get("predicate", "")))
            s_res = entity_slug(str(r.get("subject", "")), tickers)
            o_res = entity_slug(str(r.get("object", "")), tickers)
            if not pred or len(pred) < 3 or not s_res or not o_res:
                continue
            s_slug, s_name = s_res
            o_slug, o_name = o_res
            if s_slug == o_slug:
                continue
            quote, verdict = quote_gate(r, s_name, o_name, pred, page_n)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            if args.dry_run:
                mark = "KEEP" if quote else f"DROP {verdict}"
                log(f"  {mark:28} {s_name} --{pred}--> {o_name} | {str(r.get('quote') or '')[:110]!r}")
            if quote:
                kept.append((s_slug, s_name, pred, o_slug, o_name, quote))
        if args.dry_run:
            continue

        for s_slug, s_name, pred, o_slug, o_name, quote in kept:
            # 1) ensure both entity pages exist (ticker/ pages already do)
            for eslug, ename in ((s_slug, s_name), (o_slug, o_name)):
                if eslug.startswith("entities/") and eslug not in ent_pages:
                    rc, out = gb(["put", eslug], stdin=ENTITY_PAGE_TMPL.format(name=ename or eslug.split("/", 1)[1]))
                    if rc == 0:
                        ent_pages.add(eslug)
                    else:
                        log(f"  put FAILED {eslug}: {out.strip()[:120]}")
            # 2) the typed edge itself
            key = (s_slug, o_slug, pred)
            if key in edges:
                continue
            # The receipt travels with the edge: the verbatim sentence and the page it came from.
            context = f"\u201c{quote[:300]}\u201d \u2014 {slug}"
            rc, out = gb(["link", s_slug, o_slug, "--link-type", pred, "--link-source", LINK_SOURCE,
                          "--context", context])
            if rc == 0:
                edges.add(key)
                new_edges += 1
            else:
                log(f"  link FAILED {s_slug} --{pred}--> {o_slug}: {out.strip()[:120]}")

        done_pages.add(slug)
        st.update(pages=sorted(done_pages), entity_pages=sorted(ent_pages),
                  edges=[list(e) for e in edges])
        save_state(st)

    if not args.dry_run:
        st.update(pages=sorted(done_pages), entity_pages=sorted(ent_pages),
                  edges=[list(e) for e in edges])
        save_state(st)
    kept_n = verdicts.get("ok", 0)
    dropped = {k: v for k, v in sorted(verdicts.items(), key=lambda kv: -kv[1]) if k != "ok"}
    log(f"quote gate: {kept_n} of {sum(verdicts.values())} proposed relations kept; dropped {dropped or 'none'}")
    log(f"DONE — pages processed: {len(candidates)}, new edges: {new_edges}, total edges: {len(edges)}")


if __name__ == "__main__":
    main()
