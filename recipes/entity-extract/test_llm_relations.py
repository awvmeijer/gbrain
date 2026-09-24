"""Tests for the relations pass: the quote gate and the candidate-sentence step.

    python -m pytest recipes/entity-extract/test_llm_relations.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("llm_relations", Path(__file__).parent / "llm_relations.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)

ROWS = [
    ("ticker/nvda", "NVDA"), ("ticker/mu", "MU"), ("ticker/sofi", "SOFI"), ("ticker/ma", "MA"),
    ("ticker/tgt", "TGT"),
    ("entities/openai", "OpenAI"), ("entities/sam-altman", "Sam Altman"),
    ("entities/later-this-year", "later this year"), ("entities/2026", "2026"),
    ("ticker/aapl", "AAPL"), ("entities/compute", "Compute"), ("entities/ipo", "IPO"), ("entities/nvda", "NVDA"),
]
SEC = {"NVDA": "NVIDIA CORP", "MU": "MICRON TECHNOLOGY INC", "SOFI": "SoFi Technologies, Inc.",
       "MA": "Mastercard Inc", "TGT": "TARGET CORP", "AAPL": "Apple Inc."}
ENGLISH = frozenset({"apple", "micron", "compute", "target", "the", "space"})


def matcher():
    return lr.Matcher(*lr.build_dictionary(ROWS, SEC, ENGLISH))


# ---------------------------------------------------------------- dictionary

def test_sec_titles_become_names_with_a_first_word_alias():
    names, symbols, _ = lr.build_dictionary(ROWS, SEC, ENGLISH)
    assert names["nvidia"] == "ticker/nvda"
    assert names["micron technology"] == "ticker/mu" and names["micron"] == "ticker/mu"
    assert names["sofi technologies"] == "ticker/sofi" and names["mastercard"] == "ticker/ma"
    assert symbols["NVDA"] == "ticker/nvda"


def test_everyday_words_and_phrase_stubs_are_not_names():
    names, _, proper = lr.build_dictionary(ROWS, SEC, ENGLISH)
    assert "target" not in names          # TARGET CORP would match all over prose
    assert "later this year" not in names  # an old open-schema stub, not a name
    assert "2026" not in names
    assert "compute" not in names         # an everyday word as a whole stub name
    assert "ipo" not in names              # jargon, not an entity
    assert names.get("nvda") is None       # a stub named like a ticker duplicates the ticker
    assert {"apple", "micron"} <= proper   # company names that are also words: capitalised only
    assert names["openai"] == "entities/openai" and names["sam altman"] == "entities/sam-altman"


# ---------------------------------------------------------------- matching

def test_finds_names_symbols_and_cashtags_with_their_written_form():
    found = matcher().find("Nvidia supplies OpenAI, and $mu rallied while SOFI held.")
    assert found == {"ticker/nvda": "Nvidia", "entities/openai": "OpenAI", "ticker/mu": "mu",
                     "ticker/sofi": "SOFI"}


def test_jargon_capitals_are_not_symbols_and_names_need_word_boundaries():
    found = matcher().find("The CEO said GPU demand at Nvidiana is unrelated to OpenAI-like firms.")
    # no NVDA inside "Nvidiana", no CEO/GPU symbols; a hyphen is a boundary, so OpenAI counts
    assert found == {"entities/openai": "OpenAI"}
    assert matcher().find("an openai-style model") == {}  # a stub's name must be written as a name


def test_word_names_only_count_when_capitalised():
    m = matcher()
    assert m.find("Apple buys more Micron memory.") == {"ticker/aapl": "Apple", "ticker/mu": "Micron"}
    assert m.find("an apple a day, measured to the micron") == {}


def test_candidates_need_two_distinct_entities():
    body = ("Nvidia is up 4% today.\n"
            "SoFi and Mastercard announced that stablecoin settlement is live.\n"
            "Nvidia and NVDA are the same company, so this pairs nothing.\n"
            "Sam Altman said OpenAI will buy more Nvidia systems next year.")
    cands = lr.candidates(body, matcher())
    assert [c["sentence"][:4] for c in cands] == ["SoFi", "Sam "]
    assert set(cands[1]["entities"]) == {"entities/sam-altman", "entities/openai", "ticker/nvda"}


def test_sentences_strip_markdown_and_skip_fragments():
    s = lr.sentences("- **SoFi** and [Mastercard](https://x.y) partner on cards.\n\nok.\n> Micron ships HBM3E to Nvidia for Rubin.")
    assert s == ["**SoFi** and Mastercard partner on cards.", "Micron ships HBM3E to Nvidia for Rubin."]


# ---------------------------------------------------------------- model answers

def test_resolve_pair_accepts_only_entities_from_that_sentence():
    cands = lr.candidates("SoFi and Mastercard announced a partnership on stablecoin cards.\n"
                          "Micron ships HBM3E to Nvidia under a multi-year supply agreement.", matcher())
    ok = lr.resolve_pair({"i": 2, "subject": "micron", "predicate": "supplies", "object": "Nvidia"}, cands)
    assert ok and ok[1:] == ("ticker/mu", "Micron", "ticker/nvda", "Nvidia")
    assert lr.resolve_pair({"i": 1, "subject": "Micron", "object": "Nvidia"}, cands) is None  # wrong sentence
    assert lr.resolve_pair({"i": 0, "subject": "SoFi", "object": "Mastercard"}, cands) is None  # no [-1]
    assert lr.resolve_pair({"i": "x", "subject": "SoFi", "object": "Mastercard"}, cands) is None
    assert lr.resolve_pair({"i": 1, "subject": "SoFi", "object": "SoFi"}, cands) is None


def test_the_supplied_sentence_still_goes_through_the_gate():
    sent = "Micron ships HBM3E to Nvidia under a multi-year supply agreement."
    page_n = lr._norm("# Notes\n\n" + sent)
    assert lr.quote_gate({"quote": sent}, "Micron", "Nvidia", "supplies", page_n)[1] == "ok"
    assert lr.quote_gate({"quote": sent}, "Micron", "Nvidia", "acquired", page_n)[1] == "predicate_not_in_quote"
    assert lr.quote_gate({"quote": sent}, "Micron", "Nvidia", "owns", page_n)[1] == "predicate_not_in_schema"


def test_role_predicates_need_the_person_next_to_the_role():
    page = "Bill McDermott, CEO of ServiceNow, said on the earnings call that Nvidia is a key partner."
    page_n = lr._norm(page)
    assert lr.quote_gate({"quote": page}, "Bill McDermott", "ServiceNow", "ceo_of", page_n)[1] == "ok"
    assert lr.quote_gate({"quote": page}, "Nvidia", "ServiceNow", "ceo_of", page_n)[1] == "role_subject_not_adjacent"


def test_ingest_slug_matches_gbrain_import():
    assert lr.ingest_slug("capture/2026-07-01T152411-trigger-trades-c.md") == "capture/2026-07-01t152411-trigger-trades-c"
    assert lr.ingest_slug("telegram/1---technical-scanner/2026-07-13.md") == "telegram/1-technical-scanner/2026-07-13"
    assert lr.ingest_slug("youtube/stevenfiorillo1/--5UK4iO56E.md") == "youtube/stevenfiorillo1/5uk4io56e"


def test_hedged_sentences_and_reversed_passives_are_dropped():
    def gate(q, s, o, pred):
        return lr.quote_gate({"quote": q}, s, o, pred, lr._norm(q))[1]
    assert gate("SpaceX-Tesla merger described as a likely possibility", "SpaceX", "Tesla", "merged_with") == "hedged"
    assert gate("IRDM — Being acquired by RKLB at ~$8B EV; deal closes mid-2027", "IRDM", "RKLB", "acquired") \
        == "passive_reversed"
    assert gate("Rocket Lab's $8B acquisition of Iridium closes mid-2027.", "Rocket Lab", "Iridium", "acquired") == "ok"
    assert gate("Generac signed a long-term agreement to supply generators for Amazon data centers, "
                "with payments potentially reaching $8B.", "Generac", "Amazon", "supplies") == "ok"
