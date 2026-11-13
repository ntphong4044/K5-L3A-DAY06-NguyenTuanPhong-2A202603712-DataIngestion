"""PostgreSQL + pgvector: lưu chunk + vector, upsert không trùng, tìm cosine, lọc metadata bằng SQL."""
from __future__ import annotations

import json
from typing import Any

import psycopg

from .models import Chunk

TABLE = "chunks"
# Chỉ các cột này được dùng làm filter (whitelist -> không bị SQL injection qua tên cột).
FILTERABLE = {"phan": "phan", "muc": "muc", "document_id": "document_id"}


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions
        self.conn = psycopg.connect(database_url, autocommit=True)

    def create_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    strategy      text    NOT NULL,      -- index A hoặc B
                    chunk_id      text    NOT NULL,
                    document_id   text    NOT NULL,
                    source        text    NOT NULL,
                    page          integer NOT NULL,      -- trang PDF vật lý
                    printed_page  integer,               -- số trang in (STSV * n)
                    phan          text    NOT NULL,      -- "1", "2.1".."2.8", "3"
                    phan_title    text,
                    muc           text,                  -- "Điều 9" hoặc "Mục XII"
                    muc_title     text,
                    text          text    NOT NULL,
                    char_len      integer NOT NULL,
                    content_hash  text    NOT NULL,
                    metadata      jsonb   NOT NULL,
                    embedding     vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )""")
            cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_filter_idx ON {TABLE} (strategy, phan)")
            cur.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass "
                        "AND attname = 'embedding'", (TABLE,))
            dim = cur.fetchone()[0]
            if dim != self.dimensions:
                raise RuntimeError(f"Table has vector({dim}) but model gives {self.dimensions}")

    def existing_hashes(self, strategy: str) -> dict[str, str]:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT chunk_id, content_hash FROM {TABLE} WHERE strategy = %s", (strategy,))
            return dict(cur.fetchall())

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        """Ghi mới hoặc cập nhật theo khóa (strategy, chunk_id) -> chạy lại không bị nhân đôi."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        for v in vectors:
            if len(v) != self.dimensions:
                raise ValueError(f"vector has {len(v)} dims, expected {self.dimensions}")
        with self.conn.transaction(), self.conn.cursor() as cur:
            for c, v in zip(chunks, vectors):
                m = c.metadata
                cur.execute(f"""
                    INSERT INTO {TABLE} (strategy, chunk_id, document_id, source, page, printed_page,
                        phan, phan_title, muc, muc_title, text, char_len, content_hash, metadata, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                    ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                        document_id = EXCLUDED.document_id, source = EXCLUDED.source,
                        page = EXCLUDED.page, printed_page = EXCLUDED.printed_page,
                        phan = EXCLUDED.phan, phan_title = EXCLUDED.phan_title,
                        muc = EXCLUDED.muc, muc_title = EXCLUDED.muc_title,
                        text = EXCLUDED.text, char_len = EXCLUDED.char_len,
                        content_hash = EXCLUDED.content_hash, metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding""",
                    (strategy, c.chunk_id, c.document_id, c.source, c.page, m.get("printed_page"),
                     m["phan"], m.get("phan_title"), m.get("muc"), m.get("muc_title"), c.text,
                     len(c.text), m["content_hash"], json.dumps(m, ensure_ascii=False), _vec(v)))
        return len(chunks)

    def delete_obsolete(self, strategy: str, document_ids: set[str], keep_ids: set[str]) -> int:
        """Xóa chunk cũ không còn trong lần nạp hiện tại (khi tài liệu thay đổi)."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(f"SELECT chunk_id FROM {TABLE} WHERE strategy = %s AND document_id = ANY(%s)",
                        (strategy, list(document_ids)))
            stale = [r[0] for r in cur.fetchall() if r[0] not in keep_ids]
            if stale:
                cur.execute(f"DELETE FROM {TABLE} WHERE strategy = %s AND chunk_id = ANY(%s)",
                            (strategy, stale))
            return len(stale)

    def count(self, strategy: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*), count(DISTINCT chunk_id) FROM {TABLE} WHERE strategy = %s",
                        (strategy,))
            total, distinct = cur.fetchone()
            if total != distinct:
                raise RuntimeError(f"duplicate chunk ids in strategy {strategy}")
            return total

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Tìm chính xác (quét toàn bảng) theo cosine; filter áp dụng trong SQL trước khi xếp hạng."""
        where, params = ["strategy = %s"], [strategy]
        for key, value in (filters or {}).items():
            if key not in FILTERABLE:
                raise ValueError(f"filter on {key!r} is not allowed")
            where.append(f"{FILTERABLE[key]} = %s")
            params.append(value)
        sql = (f"SELECT chunk_id, document_id, source, page, printed_page, phan, phan_title, muc, "
               f"muc_title, text, 1 - (embedding <=> %s::vector) AS score FROM {TABLE} "
               f"WHERE {' AND '.join(where)} ORDER BY embedding <=> %s::vector LIMIT %s")
        qv = _vec(query_vector)
        with self.conn.cursor() as cur:
            cur.execute(sql, [qv, *params, qv, top_k])
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
