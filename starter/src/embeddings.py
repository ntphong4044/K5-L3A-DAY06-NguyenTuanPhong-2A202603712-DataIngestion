"""Real embeddings via a local Ollama server (bge-m3). Fails loudly; no mock fallback."""
from __future__ import annotations

from typing import Protocol

import requests


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, question: str) -> list[float]: ...


class OllamaEmbedder:
    """bge-m3 needs no query/passage prefix: documents and queries are embedded the same way."""

    def __init__(self, model_name: str = "bge-m3", base_url: str = "http://127.0.0.1:11434",
                 batch_size: int = 16, timeout: int = 300) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.timeout = timeout
        try:
            tags = requests.get(f"{self.base_url}/api/tags", timeout=10).json()
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama is not reachable at {self.base_url}: {exc}") from exc
        info = {m["name"]: m for m in tags.get("models", [])}
        key = model_name if model_name in info else f"{model_name}:latest"
        if key not in info:
            raise RuntimeError(f"Model {model_name!r} not pulled in Ollama. Run: ollama pull {model_name}")
        self.model_digest = info[key]["digest"]
        self.dimensions = len(self._embed(["dimension probe"])[0])

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = requests.post(f"{self.base_url}/api/embed",
                             json={"model": self.model_name, "input": texts},
                             timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama embed failed ({resp.status_code}): {resp.text[:300]}")
        vectors = resp.json().get("embeddings")
        if not vectors or len(vectors) != len(texts):
            raise RuntimeError("Ollama returned an unexpected number of embeddings")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._embed(texts[i:i + self.batch_size]))
        return out

    def embed_query(self, question: str) -> list[float]:
        return self._embed([question])[0]
