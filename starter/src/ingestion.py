"""Đọc (parse) và làm sạch Sổ tay sinh viên PDF, giữ truy vết tới từng trang.

Mỗi trang PDF -> 1 DocumentPage. Nhận diện cấu trúc: PHẦN (1, 2.1..2.8, 3), Điều N (trong
Quy chế đào tạo) và mục La Mã I., II., ... (trong Phần 3).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import DocumentPage

MIN_CONTENT_CHARS = 200            # trang < 200 ký tự sau làm sạch -> không tính là trang nội dung
MAX_DOT_RATIO = 0.25               # trang mục lục / "ghi chép" toàn dấu chấm -> loại
_PAGE_HEADER = re.compile(r"^(?:STSV\s*\*\s*(\d+)|(\d+)\s*\*\s*STSV)$")   # "STSV * 21" / "22 * STSV"
_PHAN = re.compile(r"^PHẦN\s+(\d(?:\.\d)?)\.?\s*:?\s*(.*)$")
_DIEU = re.compile(r"^Điều\s+(\d+)\s*\.\s*(\S.*)$")
_LA_MA = re.compile(r"^([IVX]{1,5})\.\s+(\S.*)$")
PHAN_TITLES = {
    "1": "Giới thiệu về Trường ĐH Giao thông vận tải",
    "2": "Các quy chế, quy định",
    "2.1": "Quy chế đào tạo đại học", "2.2": "Quy định về chuẩn đầu ra ngoại ngữ",
    "2.3": "Quy định về đánh giá rèn luyện sinh viên", "2.4": "Quy định về kỷ luật sinh viên",
    "2.5": "Quy định về học bổng", "2.6": "Quy định về chế độ chính sách",
    "2.7": "Quy định về vay vốn tín dụng", "2.8": "Quy định quản lý sinh viên ngoại trú",
    "3": "Hướng dẫn thủ tục và dịch vụ cho sinh viên",
}


def normalize_text(text: str, stats: dict[str, int]) -> tuple[str, int | None]:
    """NFC, bỏ ký tự icon (vùng Private Use), gộp khoảng trắng, bỏ dòng header số trang.

    Trả về (text, số trang in nếu đọc được). Giữ xuống dòng vì chunker cần.
    """
    text = unicodedata.normalize("NFC", text)
    pua = len(re.findall(r"[-]", text))
    if pua:
        stats["icon_chars_removed"] = stats.get("icon_chars_removed", 0) + pua
        text = re.sub(r"[-]", " ", text)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    printed = None
    if lines:
        m = _PAGE_HEADER.match(lines[0])
        if m:
            printed = int(m.group(1) or m.group(2))
            lines = lines[1:]
            stats["page_header_removed"] = stats.get("page_header_removed", 0) + 1
    return "\n".join(lines), printed


def load_pdf(doc: dict[str, Any], root: Path, errors: list[dict[str, Any]]
             ) -> tuple[list[DocumentPage], dict[str, Any]]:
    """Đọc từng trang vật lý; trang rỗng/quá ngắn/mục lục được ghi log và loại, không bỏ âm thầm."""
    reader = PdfReader(str(root / doc["path"]))
    stats: dict[str, int] = {}
    pages: list[DocumentPage] = []
    report_pages: list[dict[str, Any]] = []
    phan, muc, muc_title = None, None, None

    for pno in range(1, len(reader.pages) + 1):
        try:
            raw = reader.pages[pno - 1].extract_text(extraction_mode=doc["extraction_mode"]) or ""
        except Exception as exc:
            errors.append({"page": pno, "error": f"extract_failed:{exc}"})
            continue
        text, printed = normalize_text(raw, stats)
        if printed is not None and printed != pno:
            errors.append({"page": pno, "error": f"printed_page_mismatch:{printed}"})

        at_start = {"phan": phan, "muc": muc, "muc_title": muc_title}
        headings: list[dict[str, Any]] = []
        offset = 0
        for line in text.split("\n"):
            mp, md, ml = _PHAN.match(line), _DIEU.match(line), _LA_MA.match(line)
            changed = False
            if mp:
                phan, muc, muc_title = mp.group(1), None, None
                changed = True
            elif md and phan == "2.1":
                muc, muc_title = f"Điều {md.group(1)}", md.group(2).strip()
                changed = True
            elif ml and phan == "3" and ml.group(2)[:1].isupper():
                muc, muc_title = f"Mục {ml.group(1)}", ml.group(2).strip()
                changed = True
            if changed:
                headings.append({"offset": offset, "phan": phan, "muc": muc, "muc_title": muc_title})
            offset += len(line) + 1

        dot_ratio = text.count(".") / max(len(text), 1)
        reason = None
        if len(text) < MIN_CONTENT_CHARS:
            reason = f"excluded_short_page:{len(text)}_chars"
        elif dot_ratio > MAX_DOT_RATIO:
            reason = f"excluded_toc_or_blank_form:dot_ratio={dot_ratio:.2f}"
        report_pages.append({"page": pno, "printed_page": printed, "raw_chars": len(raw),
                             "normalized_chars": len(text), "counted": reason is None,
                             "exclusion": reason})
        if reason:
            errors.append({"page": pno, "error": reason})
            continue
        pages.append(DocumentPage(
            document_id=doc["document_id"], source=doc["path"], page=pno, text=text,
            metadata={"printed_page": printed, "at_start": at_start, "headings": headings},
        ))
    report = {"physical_pages": len(reader.pages), "content_pages": len(pages),
              "normalization_stats": stats, "pages": report_pages}
    return pages, report


def structure_at(page: DocumentPage, offset: int) -> dict[str, Any]:
    """Phần/mục đang có hiệu lực tại vị trí ký tự `offset` của trang."""
    cur = dict(page.metadata.get("at_start") or {})
    for h in page.metadata.get("headings", []):
        if h["offset"] <= offset:
            cur = {"phan": h["phan"], "muc": h["muc"], "muc_title": h["muc_title"]}
    p = cur.get("phan") or "0"
    cur["phan"] = p
    cur["phan_title"] = PHAN_TITLES.get(p, "Lời nói đầu")
    return cur
