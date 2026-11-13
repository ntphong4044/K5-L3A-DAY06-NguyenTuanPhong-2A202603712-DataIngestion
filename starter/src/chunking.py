"""Chia chunk: A = cố định theo ký tự, B = theo cấu trúc sổ tay (Phần/Điều/mục, khoản, điểm)."""
from __future__ import annotations

import hashlib
import json
import re

from .ingestion import structure_at
from .models import Chunk, DocumentPage

_HARD = re.compile(r"^(PHẦN\s+\d|Chương\s+[IVX]|Điều\s+\d+\s*\.|[IVX]{1,5}\.\s+[A-ZĐ])")
_SOFT = re.compile(r"^(\d+(\.\d+)*\.?\s|[a-zđ][\.\)]\s|[-+*•]\s?|B\d+:|\(\d+\))")


def _make_chunk(page: DocumentPage, strategy: str, idx: int, start: int, text: str,
                heading_prefix: bool = False) -> Chunk:
    st = structure_at(page, start)
    if heading_prefix:
        # B: gắn "đường dẫn" Phần > mục vào đầu chunk để chunk tự hiểu được ngữ cảnh
        crumb = f"Phần {st['phan']}. {st['phan_title']}" if st["phan"] != "0" else st["phan_title"]
        if st.get("muc"):
            crumb += f" > {st['muc']}. {st['muc_title']}"
        text = f"[{crumb}]\n{text}"
    md = {"printed_page": page.metadata.get("printed_page"), "phan": st["phan"],
          "phan_title": st["phan_title"], "muc": st.get("muc"), "muc_title": st.get("muc_title"),
          "char_start": start, "char_end": start + len(text)}
    md["content_hash"] = hashlib.sha1(
        (text + json.dumps(md, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()
    return Chunk(chunk_id=f"{page.document_id}:{strategy}:p{page.page:02d}:{idx:02d}",
                 document_id=page.document_id, source=page.source, page=page.page,
                 text=text, metadata=md)


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Cửa sổ `size` ký tự, chồng lấn `overlap`; cố ý bỏ qua cấu trúc (baseline)."""
    if not 0 <= overlap < size:
        raise ValueError("overlap must be in [0, size)")
    text, chunks, start = page.text, [], 0
    while start < len(text):
        piece = text[start:start + size]
        if piece.strip():
            chunks.append(_make_chunk(page, "fixed", len(chunks), start, piece))
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


def _paragraphs(page: DocumentPage) -> list[tuple[int, str, bool]]:
    """Tách trang thành (offset, đoạn, hard_break). Ngắt cứng ở Phần/Chương/Điều/mục La Mã."""
    paras: list[tuple[int, str, bool]] = []
    buf: list[str] = []
    buf_start, hard, offset = 0, False, 0
    for line in page.text.split("\n"):
        is_hard = bool(_HARD.match(line))
        if not buf or is_hard or _SOFT.match(line):
            if buf:
                paras.append((buf_start, "\n".join(buf), hard))
            buf, buf_start, hard = [], offset, is_hard
        buf.append(line)
        offset += len(line) + 1
    if buf:
        paras.append((buf_start, "\n".join(buf), hard))
    return paras


def _split_long(text: str, max_size: int) -> list[tuple[int, str]]:
    pieces, start = [], 0
    while len(text) - start > max_size:
        window = text[start:start + max_size]
        cut = max(window.rfind("; "), window.rfind(";\n"), window.rfind(". "), window.rfind(".\n"))
        if cut < max_size // 2:
            cut = window.rfind("\n")
        if cut < max_size // 2:
            cut = max_size - 1
        pieces.append((start, text[start:start + cut + 1].strip()))
        start += cut + 1
    if text[start:].strip():
        pieces.append((start, text[start:].strip()))
    return pieces


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Gom các khoản/điểm liên tiếp tới max_size; luôn ngắt chunk tại tiêu đề lớn."""
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_start = 0

    def emit() -> None:
        nonlocal cur
        if cur:
            chunks.append(_make_chunk(page, "para", len(chunks), cur_start, "\n".join(cur),
                                      heading_prefix=True))
        cur = []

    for p_start, body, hard in _paragraphs(page):
        parts = _split_long(body, max_size) if len(body) > max_size else [(0, body)]
        for rel, part in parts:
            if hard or (cur and sum(len(c) + 1 for c in cur) + len(part) > max_size):
                emit()
                hard = False
            if not cur:
                cur_start = p_start + rel
            cur.append(part)
    emit()
    return chunks
