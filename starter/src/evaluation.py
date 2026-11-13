"""Automatic retrieval metrics (Hit@1, Evidence Hit@3) with full per-query traces.

Human labels (coherence, grounding, metadata correctness) are NOT inferred here; the
run only exports templates for manual labelling.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def gold_options(q: dict[str, Any]) -> list[dict[str, Any]]:
    """Primary gold plus any documented alternative evidence."""
    pages = q["gold_page"] if isinstance(q["gold_page"], list) else [q["gold_page"]]
    opts = [{"source_id": q["gold_source_id"], "pages": pages, "evidence": q["evidence_quote"]}]
    for alt in q.get("alternatives", []):
        ap = alt["gold_page"] if isinstance(alt["gold_page"], list) else [alt["gold_page"]]
        opts.append({"source_id": alt["gold_source_id"], "pages": ap,
                     "evidence": alt["evidence_quote"]})
    return opts


def is_hit(q: dict[str, Any], hit: dict[str, Any]) -> bool:
    text = norm(hit["text"])
    return any(hit["document_id"] == o["source_id"] and hit["page"] in o["pages"]
               and norm(o["evidence"]) in text for o in gold_options(q))


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    flags = [is_hit(question, h) for h in hits]
    on_gold_page = [any(h["document_id"] == o["source_id"] and h["page"] in o["pages"]
                        for o in gold_options(question)) for h in hits]
    # evidence present only when several retrieved chunks are concatenated -> split evidence
    joined = norm(" ".join(h["text"] for h in hits))
    split = (not any(flags)) and any(norm(o["evidence"]) in joined for o in gold_options(question))
    filt = question.get("filter")
    filter_ok = None
    if filt:
        filter_ok = all(all(h.get(k) == v for k, v in filt.items()) for h in hits)
    return {
        "hit_at_1": bool(flags and flags[0]),
        "hit_at_3": any(flags[:3]),
        "hit_flags": flags,
        "gold_page_in_top3": any(on_gold_page[:3]),
        "evidence_split_across_chunks": split,
        "filter_compliant_top3": filter_ok,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in traces:
        key = (t["strategy"], t["split"])
        s = out.setdefault(f"{key[0]}/{key[1]}", {"strategy": key[0], "split": key[1], "N": 0,
                                                   "hit1": 0, "hit3": 0, "gold_page_top3": 0,
                                                   "evidence_split": 0, "filter_q": 0,
                                                   "filter_ok": 0})
        r = t["result"]
        s["N"] += 1
        s["hit1"] += r["hit_at_1"]
        s["hit3"] += r["hit_at_3"]
        s["gold_page_top3"] += r["gold_page_in_top3"]
        s["evidence_split"] += r["evidence_split_across_chunks"]
        if r["filter_compliant_top3"] is not None:
            s["filter_q"] += 1
            s["filter_ok"] += r["filter_compliant_top3"]
    for s in out.values():
        s["Hit@1"] = f"{s['hit1']}/{s['N']}"
        s["EvidenceHit@3"] = f"{s['hit3']}/{s['N']}"
    return out
