"""Pipeline Day 06: ingest -> verify benchmark -> index A/B -> evaluate A/B/C.

Chạy từ thư mục starter/:  python -m src.run all
                           python -m src.run ingest | verify | index | evaluate
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from . import chunking
from .embeddings import OllamaEmbedder
from .evaluation import evaluate_query, norm, summarize
from .ingestion import load_pdf
from .models import Chunk, DocumentPage
from .store import PgVectorStore

ROOT = Path(__file__).resolve().parents[1]          # starter/
DATA, RESULTS, BENCH = ROOT / "data", ROOT / "results", ROOT / "benchmark"
PAGES_FILE = DATA / "processed" / "pages.jsonl"

CORPUS = [{
    "document_id": "stsv-utc-k64",
    "title": "Sổ tay sinh viên khóa 64 - 2023, Trường Đại học Giao thông vận tải (cập nhật APP eUTC 14/8/2023)",
    "path": "data/raw/SoTaySinhVien-UTC-K64-2023.pdf",
    "url": "https://khcb.utc.edu.vn/so-tay-sinh-vien-k64-2907404",
    "url_note": "Website Trường ĐH GTVT",
    "license": "Tài liệu công khai trên website trường, dùng cho mục đích học tập.",
    "version": "Cập nhật 14/8/2023",
    "extraction_mode": "plain",  
}]

# Index = tập chunk vật lý trong pgvector. C dùng lại index B, chỉ thêm filter.
INDEXES = {
    "A": {"chunker": "fixed_size", "size": 500, "overlap": 50},
    "B": {"chunker": "by_paragraph", "max_size": 900, "heading_prefix": True}  # B: gắn [Phần > mục] vào đầu chunk,
}
STRATEGIES = {
    "A": {"index": "A", "use_filter": False},
    "B": {"index": "B", "use_filter": False},
    "C": {"index": "B", "use_filter": True},
}
TOP_K = 3


def _env() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    db = os.environ.get("DATABASE_URL")
    model = os.environ.get("EMBEDDING_MODEL") or "bge-m3"
    if not db:
        sys.exit("DATABASE_URL missing in starter/.env")
    return db, model


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_pages() -> list[DocumentPage]:
    return [DocumentPage(**json.loads(l))
            for l in PAGES_FILE.read_text(encoding="utf-8").splitlines()]


def build_chunks(pages: list[DocumentPage], cfg: dict) -> list[Chunk]:
    out: list[Chunk] = []
    for p in pages:
        if cfg["chunker"] == "fixed_size":
            out += chunking.fixed_size(p, cfg["size"], cfg["overlap"])
        else:
            out += chunking.by_paragraph(p, cfg["max_size"])
    return out


# ---------------------------------------------------------------- ingest
def cmd_ingest() -> None:
    t0 = time.perf_counter()
    errors: list[dict] = []
    all_pages: list[DocumentPage] = []
    sources, stats_by_doc = [], {}
    for doc in CORPUS:
        pages, rep = load_pdf(doc, ROOT, errors)
        all_pages += pages
        stats_by_doc[doc["document_id"]] = rep["normalization_stats"]
        sources.append({
            "source_id": doc["document_id"], "title": doc["title"],
            "source_path_or_url": doc["url"], "source_note": doc["url_note"], "local_path": doc["path"],
            "license_or_permission": doc["license"], "accessed_at": "2026-11-13",
            "version": doc["version"], "extraction": f"pypdf extraction_mode={doc['extraction_mode']}",
            "physical_pages": rep["physical_pages"], "content_pages": rep["content_pages"],
            "content_characters": sum(len(p.text) for p in pages),
            "pages": [{"page_id": f"p{r['page']:02d}", **r} for r in rep["pages"]],
        })
    total = len(all_pages)
    if total < 30:
        sys.exit(f"Only {total} content pages (< 30)")
    PAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PAGES_FILE.open("w", encoding="utf-8") as f:
        for p in all_pages:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
    _write_json(DATA / "manifest.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "page_counting_rule": "Một trang PDF được tính là trang nội dung nếu text sau làm sạch "
                              "(bỏ header 'STSV * n', gộp khoảng trắng) có >= 200 ký tự và tỷ lệ "
                              "dấu chấm <= 25% (loại mục lục và trang 'Ghi chép').",
        "total_content_pages": total, "sources": sources})
    _write_json(RESULTS / "ingest_errors.json", {"normalization_stats": stats_by_doc, "errors": errors})

    stats = {}
    for name, cfg in INDEXES.items():
        chunks = build_chunks(all_pages, cfg)
        lens = [len(c.text) for c in chunks]
        stats[name] = {"config": cfg, "chunks": len(chunks), "total_chars": sum(lens),
                       "min": min(lens), "median": statistics.median(lens),
                       "mean": round(statistics.mean(lens), 1), "max": max(lens),
                       "under_100_chars": sum(l < 100 for l in lens),
                       "by_document": {d["document_id"]: sum(c.document_id == d["document_id"] for c in chunks)
                                       for d in CORPUS}}
    _write_json(RESULTS / "chunk_stats.json", stats)
    for s in sources:
        print(f"ingest {s['source_id']}: {s['content_pages']}/{s['physical_pages']} content pages")
    print(f"total content pages: {total}; logged issues: {len(errors)}; "
          f"{time.perf_counter() - t0:.1f}s; norm stats: {stats_by_doc}")
    for k, v in stats.items():
        print(f"  index {k}: {v['chunks']} chunks, median {v['median']} chars, by doc {v['by_document']}")


# ---------------------------------------------------------------- verify benchmark
def load_questions() -> list[dict]:
    return json.loads((BENCH / "questions.json").read_text(encoding="utf-8"))["questions"]


def cmd_verify() -> None:
    from .evaluation import gold_options
    pages = {(p.document_id, p.page): p for p in load_pages()}
    qs = load_questions()
    problems = []
    for q in qs:
        for o in gold_options(q):
            if not any(norm(o["evidence"]) in norm(pages[(o["source_id"], g)].text)
                       for g in o["pages"] if (o["source_id"], g) in pages):
                problems.append(f"{q['id']}:{o['source_id']}:{o['pages']}")
    splits = {s: sum(q["split"] == s for q in qs) for s in ("dev", "test")}
    filt = {s: sum(q["split"] == s and bool(q.get("filter")) for q in qs) for s in ("dev", "test")}
    report = {"questions": len(qs), "splits": splits, "filter_questions": filt,
              "evidence_not_found": problems}
    _write_json(RESULTS / "benchmark_verification.json", report)
    print("verify:", json.dumps(report, ensure_ascii=False))
    if problems or splits["dev"] < 15 or splits["test"] < 5 or sum(filt.values()) < 3 or filt["test"] < 1:
        sys.exit("benchmark verification FAILED")


# ---------------------------------------------------------------- index
def cmd_index() -> None:
    db, model = _env()
    pages = load_pages()
    emb = OllamaEmbedder(model)
    store = PgVectorStore(db, emb.dimensions)
    store.create_schema()
    log = {"model": model, "model_digest": emb.model_digest, "dimensions": emb.dimensions,
           "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "indexes": {}}
    doc_ids = {p.document_id for p in pages}
    for name, cfg in INDEXES.items():
        t0 = time.perf_counter()
        chunks = build_chunks(pages, cfg)
        existing = store.existing_hashes(name)
        todo = [c for c in chunks if existing.get(c.chunk_id) != c.metadata["content_hash"]]
        t_emb = time.perf_counter()
        vectors = emb.embed_documents([c.text for c in todo]) if todo else []
        t_emb = time.perf_counter() - t_emb
        store.upsert(name, todo, vectors)
        deleted = store.delete_obsolete(name, doc_ids, {c.chunk_id for c in chunks})
        total = store.count(name)
        log["indexes"][name] = {
            "config": cfg, "chunks_current": len(chunks), "embedded_this_run": len(todo),
            "unchanged_skipped": len(chunks) - len(todo), "deleted_obsolete": deleted,
            "rows_in_db": total, "embed_seconds": round(t_emb, 2),
            "total_seconds": round(time.perf_counter() - t0, 2), "cost_usd": 0.0}
        print(f"index {name}: {len(chunks)} chunks, embedded {len(todo)}, deleted {deleted}, "
              f"rows {total}, {log['indexes'][name]['total_seconds']}s")
    runs_path = RESULTS / "index_runs.json"
    runs = json.loads(runs_path.read_text(encoding="utf-8")) if runs_path.exists() else []
    runs.append(log)
    _write_json(runs_path, runs)


# ---------------------------------------------------------------- evaluate
def cmd_evaluate() -> None:
    db, model = _env()
    emb = OllamaEmbedder(model)
    store = PgVectorStore(db, emb.dimensions)
    qs = load_questions()
    qvecs = {q["id"]: emb.embed_query(q["question"]) for q in qs}
    all_traces = []
    for sname, scfg in STRATEGIES.items():
        traces = []
        for q in qs:
            filt = q.get("filter") if scfg["use_filter"] else None
            t0 = time.perf_counter()
            hits = store.search(scfg["index"], qvecs[q["id"]], TOP_K, filt)
            ms = (time.perf_counter() - t0) * 1000
            traces.append({
                "strategy": sname, "index": scfg["index"], "id": q["id"], "split": q["split"],
                "type": q.get("type"), "question": q["question"],
                "filter_applied": filt, "question_filter": q.get("filter"),
                "gold_source_id": q["gold_source_id"], "gold_page": q["gold_page"],
                "alternatives": q.get("alternatives", []),
                "evidence_quote": q["evidence_quote"], "expected_answer": q["expected_answer"],
                "search_ms": round(ms, 1),
                "top_k": [{**h, "score": round(float(h["score"]), 4)} for h in hits],
                "result": evaluate_query(q, hits),
            })
        with (RESULTS / f"traces_{sname}.jsonl").open("w", encoding="utf-8") as f:
            for t in traces:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        all_traces += traces
    summary = summarize(all_traces)
    _write_json(RESULTS / "summary.json", {"model": model, "dimensions": emb.dimensions,
                                           "top_k": TOP_K, "strategies": STRATEGIES,
                                           "indexes": INDEXES, "metrics": summary})
    print(f"{'strategy/split':<12} {'Hit@1':>7} {'Hit@3':>7} {'goldpg':>7} {'split':>6} {'filter':>7}")
    for k, s in summary.items():
        fc = f"{s['filter_ok']}/{s['filter_q']}" if s["filter_q"] else "N/A"
        print(f"{k:<12} {s['Hit@1']:>7} {s['EvidenceHit@3']:>7} "
              f"{s['gold_page_top3']:>3}/{s['N']:<3} {s['evidence_split']:>6} {fc:>7}")
    export_manual_templates(all_traces)


def export_manual_templates(traces: list[dict]) -> None:
    """File CSV để chấm tay; các cột nhãn để trống có chủ đích."""
    man = RESULTS / "manual"
    man.mkdir(parents=True, exist_ok=True)
    # Không ghi đè nhãn đã chấm tay: nếu file đã tồn tại thì ghi bản mới ra *_new.csv
    existing = [p.name for p in man.glob("*.csv") if not p.stem.endswith("_new")]
    if existing:
        print(f"manual files exist {existing} -> writing *_new.csv instead (labels kept)")
    suffix = "_new" if existing else ""
    with (man / f"grounding_metadata_audit{suffix}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["chien_luoc", "ma_cau", "cau_hoi", "dap_an_mong_doi", "filter", "hang", "chunk_id",
                    "trang_pdf", "trang_in", "phan", "muc", "diem_cosine",
                    "may_cham_hit", "noi_dung_chunk",
                    "meta_trang_dung(1/0)", "meta_phan_muc_dung(1/0)", "ghi_chu_meta",
                    "grounding_0_1_2 (chỉ điền ở dòng hang=1, chấm cả top-3)", "ly_do_grounding"])
        for t in traces:
            if t["split"] != "test":
                continue
            for r, h in enumerate(t["top_k"], 1):
                w.writerow([t["strategy"], t["id"], t["question"], t["expected_answer"],
                            json.dumps(t["filter_applied"], ensure_ascii=False), r, h["chunk_id"],
                            h["page"], h["printed_page"], h["phan"],
                            h["muc"], h["score"], int(t["result"]["hit_flags"][r - 1]), h["text"],
                            "", "", "", "", ""])
    # Coherence: 10 vị trí chọn trước khi xem kết quả = điểm giữa của 10 trang nội dung
    # cách đều nhau (trang nội dung thứ 0, 7, 14, ... theo thứ tự trang)
    pages = load_pages()
    step = max(len(pages) // 10, 1)
    chosen = pages[::step][:10]
    with (man / f"coherence{suffix}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["vi_tri", "trang_pdf", "offset", "index", "chien_luoc", "chunk_id",
                    "noi_dung_chunk", "coherence_0_1_2", "ly_do"])
        for idx_name, cfg in INDEXES.items():
            chunks = build_chunks(chosen, cfg)
            for p in chosen:
                off = len(p.text) // 2
                cands = [c for c in chunks if c.document_id == p.document_id and c.page == p.page
                         and c.metadata["char_start"] <= off]
                c = max(cands, key=lambda c: c.metadata["char_start"]) if cands else None
                w.writerow([f"p{p.page:02d}", p.page, off, idx_name,
                            "A" if idx_name == "A" else "B,C (cùng chunk)",
                            c.chunk_id if c else "", c.text if c else "", "", ""])
    print(f"manual templates -> {man}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"ingest": [cmd_ingest], "verify": [cmd_verify], "index": [cmd_index],
             "evaluate": [cmd_evaluate], "all": [cmd_ingest, cmd_verify, cmd_index, cmd_evaluate]}
    if cmd not in steps:
        sys.exit(f"unknown command {cmd}; use one of {list(steps)}")
    for step in steps[cmd]:
        step()


if __name__ == "__main__":
    main()
