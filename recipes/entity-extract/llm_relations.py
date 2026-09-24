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

Candidate sentences (2026-09-24): the model no longer reads whole pages. Code
first finds sentences that name two or more KNOWN entities — ticker hubs (by
symbol and by their SEC company name) and existing entity pages — and the model
only labels the pair in each sentence with a schema predicate, or nothing. The
quote is the sentence code supplied, so it is always verbatim; the gate still
checks entities, predicate stems and role adjacency. Pages are ranked by how many
candidate sentences they hold, so the nightly budget goes where the pairs are.
(Whole-page mode, first shadow night: 200 pages → 5 proposals → 1 past the gate.)

Idempotent: logs/llm-relations-state.json records processed pages and
(src, dst, type) edges — re-runs are no-ops. Both ends of every edge are pages
that already exist (ticker/<sym> or entities/<slug>), so the pass no longer
mints entity stubs.

Usage:
    python llm_relations.py [--days 2] [--limit 40] [--dry-run]
                            [--model qwen3:30b-a3b] [--ollama http://127.0.0.1:11434]
"""
from __future__ import annotations

import argparse
import json
import os
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
SHADOW_LOG = GBRAIN / "logs" / "llm-relations-shadow.jsonl"  # proposals awaiting a human label
LOG = GBRAIN / "logs" / "llm-relations.log"
SEC_TICKERS = GBRAIN / "data" / "edgar" / "company_tickers.json"  # edgar-to-brain's daily cache

MAX_RELATIONS_PER_PAGE = 8
MAX_CANDIDATES_PER_PAGE = 8
SENT_MIN, SENT_MAX = 30, 400

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

SYSTEM_PROMPT = """You label relations between named entities, one sentence at a time.

You get numbered sentences, each followed by the entities named in it. For each sentence,
return a relation only if THAT sentence itself states it as fact. Opinions, predictions,
questions, comparisons and price talk are not relations.

- predicate: EXACTLY one of these (subject → object, direction matters):
{schema}
  If the sentence states none of these, return nothing for it.
- subject and object: copied exactly from that sentence's entity list.
- i: the sentence number.
- At most {max_rel} relations in total. Fewer is better than doubtful.

Answer with JSON ONLY, exactly this shape (an empty list is a good answer):
{{"relations": [{{"i": 1, "subject": "Nvidia", "predicate": "supplies", "object": "OpenAI"}}]}}"""


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


def norm_predicate(p: str) -> str:
    p = re.sub(r"[^a-z0-9]+", "_", (p or "").lower()).strip("_")
    return p[:40]


# ---------------------------------------------------------------- quote gate
#
# 2026-09-23: without a check the pass wrote "Nvidia owns NBIS", "Nvidia ceo_of
# ServiceNow", "Nvidia acquires IREN" — 1,063 edges, none with a receipt. Now the
# model must return the sentence that states the relation, and an edge is written
# only if code confirms: (1) the quote is verbatim in the page, (2) both entities
# are named in it, (3) the predicate's verb is grounded in it. Precision over
# recall: a doubtful edge is dropped, and every kept edge carries its quote.

# Closed relation schema (2026-09-23). Open-schema extraction produced 350
# predicate types for 1,063 edges and got direction and meaning wrong often
# enough that no after-the-fact check reached 90% precision. Every predicate here
# has a definition and a direction; anything else is dropped.
RELATION_SCHEMA = {
    "acquired": "subject company acquired or agreed to buy the object company or asset",
    "invested_in": "subject invested in, or holds an equity stake in, the object",
    "supplies": "subject sells or provides products/services to the object (object is the customer)",
    "contract_with": "subject signed a named contract or deal with the object",
    "partners_with": "subject and object announced a partnership or collaboration",
    "competes_with": "subject and object are explicitly described as competitors",
    "subsidiary_of": "subject is owned by, or a unit of, the object",
    "merged_with": "subject merged or agreed to merge with the object",
    "launched": "subject company launched or released the object product",
    "sued": "subject filed a lawsuit against the object",
    "ceo_of": "subject person is the CEO of the object organisation",
    "founder_of": "subject person founded the object organisation",
    "works_at": "subject person works at the object organisation",
    "joins_index": "subject company joins or is added to the object index",
}
SCHEMA_TEXT = "\n".join(f"  - {k}: {v}" for k, v in RELATION_SCHEMA.items())

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
    "sued": ("sue", "lawsuit", "litigat", "court"),
    "contract_with": ("contract", "deal", "agreement", "signed", "award"),
    "merged_with": ("merg", "combin", "amalgam"),
    "founder_of": ("found", "co-found", "cofound", "started"),
    "joins_index": ("join", "added", "inclusion", "includ", "enter", "replac"),
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


HEDGE = re.compile(r"(?<![a-z])(likely|possibilit\w*|rumou?r\w*|speculat\w*|flagged|in talks|could|would)(?![a-z])")
PASSIVE = {
    "acquired": r"(acquired|bought|purchased|taken over) by",
    "invested_in": r"(backed|funded|financed) by",
    "supplies": r"(supplied|provided|powered) by",
    "sued": r"sued by",
    "launched": r"launched by",
}


def _passive_between(s_name: str, o_name: str, verb: str, quote_n: str) -> bool:
    s_n, o_n = _norm(s_name).lstrip("$"), _norm(o_name).lstrip("$")
    i, j = quote_n.find(s_n), quote_n.rfind(o_n)
    return 0 <= i < j and re.search(verb, quote_n[i + len(s_n):j]) is not None


def _stem_in(stem: str, quote_n: str) -> bool:
    """Stem at a word start: "ship" must not match inside "partnership"."""
    return re.search(rf"(?<![a-z0-9]){re.escape(stem.strip())}", quote_n) is not None


def quote_gate(rel: dict, s_name: str, o_name: str, pred: str, page_n: str) -> tuple[str | None, str]:
    """(quote, "ok") if the relation is grounded in the page, else (None, reason)."""
    if pred not in RELATION_SCHEMA:
        return None, "predicate_not_in_schema"
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
    # A sentence that hedges states a possibility, not a relation ("merger described
    # as a likely possibility", "vendor financing flagged as upcoming").
    if HEDGE.search(quote_n):
        return None, "hedged"
    # Passive voice reverses direction: "IRDM — being acquired by RKLB" is RKLB
    # acquired IRDM. Drop when the passive verb sits between subject and object.
    if pred in PASSIVE and _passive_between(s_name, o_name, PASSIVE[pred], quote_n):
        return None, "passive_reversed"
    # Role predicates name a PERSON's role: the subject must sit right next to the
    # role word ("Bill McDermott, CEO of ServiceNow" must not become Nvidia ceo_of
    # ServiceNow just because Nvidia appears later in the sentence).
    if pred in ROLE_PREDICATES and not _near(s_name, stems, quote_n, ROLE_WINDOW):
        return None, "role_subject_not_adjacent"
    return quote, "ok"


ROLE_PREDICATES = {"ceo_of", "founder_of", "works_at"}
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
    """(title, body-without-frontmatter)."""
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
    return title, body.strip()


# ------------------------------------------------------- candidate sentences

def ingest_slug(rel: str) -> str:
    """The slug `gbrain import` gives an ingest path: lowercased, runs of hyphens
    collapsed, hyphens trimmed per segment (`youtube/c/-AbC` → `youtube/c/abc`)."""
    rel = rel[:-3] if rel.endswith(".md") else rel
    return "/".join(re.sub(r"-{2,}", "-", seg).strip("-") for seg in rel.lower().split("/"))


# SEC company titles → the name text uses: "NVIDIA CORP" → "nvidia",
# "Micron Technology, Inc." → "micron technology".
_COMPANY_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|companies|ltd|limited|plc|holdings?|group|"
    r"n\.?v|s\.?a|ag|se|llc|l\.?p|trust|class [a-z]|common stock|ordinary shares|ads|adr)\b\.?"
    r"|/[a-z]{2,3}/?|[.,&]", re.I)
# A single-word name that is also an everyday word would match all over prose.
_GENERIC_NAMES = {
    "target", "block", "visa", "gap", "match", "ball", "best", "bill", "box", "cars", "check", "compass",
    "digital", "energy", "equity", "first", "future", "general", "global", "gold", "golden", "group",
    "harmony", "international", "life", "live", "lucid", "mobile", "national", "news", "next", "ocean",
    "open", "par", "power", "progressive", "rocket", "sea", "sonic", "southern", "square", "star",
    "sun", "super", "trade", "united", "unity", "universal", "vital", "western", "american", "applied",
    "advanced", "micro", "the", "new", "one", "data", "cloud", "core", "prime", "home", "health", "arm",
    "blue", "real", "good", "true", "play", "fast", "high", "light", "north", "south", "east", "west",
    "great", "royal", "bank", "smart", "clear", "black", "white", "green", "silver", "summit", "alpha",
    "bullish", "bearish",  # tickers exist for both; in fintwit they are always sentiment
}


def clean_company(title: str) -> str:
    n = _COMPANY_SUFFIX.sub(" ", title or "")
    return re.sub(r"\s+", " ", n).strip().lower()


def _looks_proper(title: str) -> bool:
    """An entity page title that reads as a name ("COMAC", "SoFi", "Sam Altman"),
    not a phrase the old open-schema pass minted ("later this year", "2026")."""
    words = title.split()
    return (3 <= len(title) <= 40 and 1 <= len(words) <= 4 and _is_entity_name(title)
            and title.lower() not in ENTITY_STOP
            and all(w[0].isupper() or w[0].isdigit() for w in words))


def build_dictionary(rows: list[tuple[str, str]], sec: dict[str, str],
                     english: frozenset[str] = frozenset()) -> tuple[dict, dict, set]:
    """(names, symbols, proper). names: normalized name → entity slug; symbols:
    SYMBOL → ticker slug; proper: names that are also everyday English words
    ("apple", "micron", "oracle") and so only count when capitalised in the text.
    Tickers win a name collision with an entity page."""
    names: dict[str, str] = {}
    symbols: dict[str, str] = {}
    proper: set[str] = set()

    def add(n: str, slug: str) -> None:
        if len(n) < 3 or n in _GENERIC_NAMES or n.upper() in CASHTAG_STOP_WORDS or n in names:
            return
        names[n] = slug
        if " " not in n and n in english:
            proper.add(n)

    for slug, title in rows:
        if slug.startswith("ticker/"):
            sym = slug.split("/", 1)[1].upper()
            symbols[sym] = slug
            full = clean_company(sec.get(sym, ""))
            add(full, slug)
            words = full.split()
            if len(words) >= 2 and len(words[0]) >= 4:
                add(words[0], slug)  # "micron technology" → also "micron"
    for slug, title in rows:
        if slug.startswith("entities/") and title and _looks_proper(title):
            n = _norm(title)
            # an everyday word as a whole entity name is an old open-schema stub
            # ("Compute", "Revenue", "Space"); a stub named like a ticker duplicates it
            if (" " not in n and n in english) or n.upper() in symbols:
                continue
            add(n, slug)
            if names.get(n) == slug:
                proper.add(n)  # a stub's name must be written as a name ("Data Center", not "data center")
    return names, symbols, proper


def english_words() -> frozenset[str]:
    try:
        return frozenset(w.strip().lower() for w in open("/usr/share/dict/words", errors="ignore"))
    except OSError:
        return frozenset()


def load_dictionary() -> tuple[dict, dict, set]:
    """Ticker hubs and entity pages from the brain (one read-only query) + SEC names."""
    url = json.loads((HOME / ".gbrain" / "config.json").read_text())["database_url"]
    r = subprocess.run(
        ["psql", url, "-At", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-c",
         "SELECT slug, coalesce(title, '') FROM pages WHERE deleted_at IS NULL "
         "AND (slug LIKE 'ticker/%' OR slug LIKE 'entities/%')"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PGOPTIONS": "-c default_transaction_read_only=on"})
    if r.returncode != 0:
        raise SystemExit(f"[llm-relations] cannot read entity pages: {r.stderr.strip()[:200]}")
    rows = [tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l]
    try:
        sec = {v["ticker"].upper(): v["title"] for v in json.loads(SEC_TICKERS.read_text()).values()}
    except (OSError, ValueError, KeyError):
        sec = {}
    return build_dictionary(rows, sec, english_words())


def sentences(text: str) -> list[str]:
    """Sentences of a markdown body: split on terminal punctuation and on lines,
    list markers and link syntax removed, whitespace collapsed."""
    out = []
    for part in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        part = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", part)          # [text](url) → text
        part = re.sub(r"^\s*(?:[-*+>]|\d+[.)])\s+", "", part)           # list / quote markers
        part = re.sub(r"\s+", " ", part).strip()
        if SENT_MIN <= len(part) <= SENT_MAX:
            out.append(part)
    return out


_SYMBOL = re.compile(r"\$([A-Za-z]{1,5})\b|(?<![A-Za-z0-9$])([A-Z]{3,5})(?![A-Za-z0-9])")


class Matcher:
    """Finds known entities in a sentence: names (case-insensitive, whole words,
    longest first; everyday-word names only when capitalised) and ticker symbols
    ($any-case, or bare 3-5 capitals)."""

    def __init__(self, names: dict[str, str], symbols: dict[str, str], proper: set[str] | None = None) -> None:
        self.names, self.symbols, self.proper = names, symbols, proper or set()
        alts = sorted(names, key=len, reverse=True)
        self.rx = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, alts)) + r")(?![A-Za-z0-9])",
                             re.I) if alts else None

    def find(self, sentence: str) -> dict[str, str]:
        """slug → the entity's surface form as written in the sentence."""
        found: dict[str, str] = {}
        if self.rx:
            for m in self.rx.finditer(sentence):
                surf = m.group(1)
                key = _norm(surf)
                if key in self.proper and not surf[0].isupper():
                    continue  # "the apple of", "space", "compute"
                if key in self.names:
                    found.setdefault(self.names[key], surf)
        for m in _SYMBOL.finditer(sentence):
            sym = (m.group(1) or m.group(2)).upper()
            if sym in self.symbols and sym not in CASHTAG_STOP_WORDS:
                found.setdefault(self.symbols[sym], m.group(1) or m.group(2))
        return found


# Bare capitals that are words or jargon, not symbols ("CEO", "GPU", "USA").
CASHTAG_STOP_WORDS = {
    "CEO", "CFO", "COO", "CTO", "GPU", "CPU", "USA", "USD", "EUR", "IPO", "ETF", "EPS", "GDP", "FED",
    "SEC", "FDA", "DOE", "DOD", "API", "LLM", "AGI", "ATH", "YTD", "EOD", "IMO", "LOL", "THE", "AND",
    "FOR", "NOT", "ALL", "NEW", "BUY", "NOW", "TOP", "BIG", "HBM", "DRAM", "AI", "EV", "NFT", "ESG",
}


def candidates(body: str, matcher: Matcher) -> list[dict]:
    """Sentences naming ≥2 distinct known entities, first MAX_CANDIDATES_PER_PAGE."""
    out, seen = [], set()
    for sent in sentences(body):
        key = _norm(sent)
        if key in seen:
            continue
        found = matcher.find(sent)
        if len(found) >= 2:
            seen.add(key)
            out.append({"sentence": sent, "entities": found})
            if len(out) >= MAX_CANDIDATES_PER_PAGE:
                break
    return out


def candidate_prompt(title: str, cands: list[dict]) -> str:
    lines = [f"Title: {title}", ""]
    for i, c in enumerate(cands, 1):
        lines.append(f"[{i}] {c['sentence']}")
        lines.append("    entities: " + "; ".join(dict.fromkeys(c["entities"].values())))
    return "\n".join(lines)


def resolve_pair(rel: dict, cands: list[dict]) -> tuple[dict, str, str, str, str] | None:
    """(candidate, subject slug, subject name, object slug, object name) for a model
    answer, or None when it points at no sentence or names an entity not in it."""
    try:
        i = int(rel.get("i"))
    except (TypeError, ValueError):
        return None
    if not 1 <= i <= len(cands):
        return None
    c = cands[i - 1]
    by_name = {v.lower(): (slug, v) for slug, v in c["entities"].items()}
    s = by_name.get(str(rel.get("subject", "")).strip().lower())
    o = by_name.get(str(rel.get("object", "")).strip().lower())
    if not s or not o or s[0] == o[0]:
        return None
    return c, s[0], s[1], o[0], o[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=2, help="only pages modified in the last N days")
    ap.add_argument("--limit", type=int, default=40, help="max pages per run (bounds nightly runtime)")
    ap.add_argument("--model", default="qwen3:30b-a3b")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--dry-run", action="store_true", help="extract + print, write nothing")
    ap.add_argument("--write", action="store_true",
                    help="write gated edges to the graph. Default is SHADOW mode: proposals go to "
                         "logs/llm-relations-shadow.jsonl for labelling, the graph is untouched")
    ap.add_argument("--only-prefix", default="", help="restrict to ingest pages under this prefix, e.g. youtube/")
    args = ap.parse_args()

    # Ollama liveness first — a dead daemon should be one log line, not 40 timeouts.
    try:
        urllib.request.urlopen(f"{args.ollama}/api/tags", timeout=5).read()
    except Exception as e:  # noqa: BLE001
        log(f"ollama not reachable at {args.ollama} ({type(e).__name__}) — skipping run")
        return

    st = load_state()
    # SHADOW (default): proposals + gate verdicts go to SHADOW_LOG for labelling and
    # the graph is untouched; shadow keeps its own progress so a later --write run
    # still processes every page. --write: gated edges go to the graph.
    shadow = not args.write and not args.dry_run
    done_key = "shadow_pages" if shadow else "pages"
    done_pages = set(st.get(done_key, []))
    edges = {tuple(e) for e in st.get("edges", [])}
    matcher = Matcher(*load_dictionary())

    # Every eligible page in the window is scanned (a regex pass, no model); the
    # model then sees the pages with the most candidate sentences first.
    cutoff = time.time() - args.days * 86400
    scanned, ranked = 0, []
    for f in ING.rglob("*.md"):
        rel = f.relative_to(ING).as_posix()
        slug = ingest_slug(rel)
        if rel.startswith(SKIP_PREFIXES) or (args.only_prefix and not rel.startswith(args.only_prefix)):
            continue
        if slug in done_pages or rel[:-3] in done_pages:  # older state used the raw relpath
            continue
        if f.stat().st_mtime < cutoff:
            continue
        scanned += 1
        title, body = page_text(f)
        cands = candidates(body, matcher)
        if cands:  # pages without a pair stay unmarked: tomorrow's dictionary may pair them
            ranked.append((len(cands), slug, f, title, cands))
    ranked.sort(key=lambda t: (-t[0], t[1]))
    todo = ranked[: args.limit]

    mode = "SHADOW (review file only)" if shadow else "DRY RUN" if args.dry_run else "WRITE"
    log(f"{mode} model={args.model} days={args.days} · dictionary {len(matcher.names)} names + "
        f"{len(matcher.symbols)} symbols · scanned {scanned} pages, {len(ranked)} with candidate "
        f"sentences ({sum(t[0] for t in ranked)}) → {len(todo)} to process "
        f"(prior: {len(done_pages)} pages, {len(edges)} graph edges)")

    system = SYSTEM_PROMPT.format(max_rel=MAX_RELATIONS_PER_PAGE, schema=SCHEMA_TEXT)
    new_edges = shadow_rows = 0
    verdicts: dict[str, int] = {}
    for _n, slug, f, title, cands in todo:
        page_n = _norm(f.read_text(errors="ignore"))  # the gate checks quotes against the whole page
        try:
            raw = ollama_chat(args.ollama, args.model, system, candidate_prompt(title, cands))
        except Exception as e:  # noqa: BLE001
            log(f"  {slug}: ollama call failed ({type(e).__name__}) — will retry next run")
            continue
        rels = parse_relations(raw)
        if args.dry_run:
            log(f"  {slug}: {len(cands)} candidate sentences, {len(rels)} labelled")
        kept = []
        for r in rels:
            pred = norm_predicate(str(r.get("predicate", "")))
            pair = resolve_pair(r, cands)
            if not pred or not pair:
                verdicts["not_a_candidate_pair"] = verdicts.get("not_a_candidate_pair", 0) + 1
                continue
            c, s_slug, s_name, o_slug, o_name = pair
            r = {**r, "quote": c["sentence"]}  # the receipt is the sentence code found, verbatim
            quote, verdict = quote_gate(r, s_name, o_name, pred, page_n)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            if args.dry_run:
                mark = "KEEP" if quote else f"DROP {verdict}"
                log(f"  {mark:28} {s_name} --{pred}--> {o_name} | {c['sentence'][:110]!r}")
            if shadow:
                shadow_rows += 1
                with SHADOW_LOG.open("a") as fh:
                    fh.write(json.dumps({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "page": slug, "model": args.model,
                        "subject": s_name, "subject_slug": s_slug, "predicate": pred,
                        "object": o_name, "object_slug": o_slug, "quote": str(r.get("quote") or "")[:QUOTE_MAX],
                        "gate": verdict, "label": None,   # label: "correct" | "wrong" (human review)
                        "mode": "candidates",  # rows before 2026-09-24 came from whole-page reading
                    }) + "\n")
            if quote:
                kept.append((s_slug, s_name, pred, o_slug, o_name, quote))
        if args.dry_run:
            continue
        if shadow:
            done_pages.add(slug)
            st[done_key] = sorted(done_pages)
            save_state(st)
            continue

        for s_slug, s_name, pred, o_slug, o_name, quote in kept:
            # both ends came from the dictionary, so both pages already exist
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
        st.update({done_key: sorted(done_pages)}, edges=[list(e) for e in edges])
        save_state(st)

    kept_n = verdicts.get("ok", 0)
    dropped = {k: v for k, v in sorted(verdicts.items(), key=lambda kv: -kv[1]) if k != "ok"}
    log(f"quote gate: {kept_n} of {sum(verdicts.values())} proposed relations passed; dropped {dropped or 'none'}")
    if shadow:
        log(f"DONE (shadow) — pages: {len(todo)}, proposals logged: {shadow_rows} → {SHADOW_LOG}")
    else:
        log(f"DONE — pages processed: {len(todo)}, new edges: {new_edges}, total edges: {len(edges)}")


if __name__ == "__main__":
    main()
