"""Rank fusion and a small exact dense index. No third-party vector database."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

from .embeddings import EmbeddingError, normalize


def rrf(rankings: list[list[tuple[str, str]]], k: int = 60) -> list[tuple[float, tuple[str, str]]]:
    if type(k) is not int or k < 1:
        raise ValueError("rrf_k must be a positive integer")
    scores: dict[tuple[str, str], float] = {}
    for ranking in rankings:
        seen = set()
        for rank, key in enumerate(ranking, 1):
            if key not in seen:
                scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
                seen.add(key)
    return sorted(((score, key) for key, score in scores.items()), key=lambda item: (-item[0], item[1]))


class DenseIndex:
    def __init__(self, entries: list[dict], corpus_sha256: str, embedder, cache_dir: Path | None = None):
        self.entries, self.embedder, self.cache_dir = entries, embedder, cache_dir
        identity = json.dumps([corpus_sha256, embedder.cache_key, "title-newline-text-v1"])
        self.fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        self.vectors = None

    def _documents(self) -> tuple[list[list[float]], str]:
        if self.vectors is not None:
            return self.vectors, "memory_hit"
        cache = self.cache_dir / (self.fingerprint + ".json") if self.cache_dir is not None else None
        cache_state = "built"
        if cache is not None and cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if (data["fingerprint"] != self.fingerprint or not isinstance(data["vectors"], list)
                        or len(data["vectors"]) != len(self.entries)):
                    raise ValueError
                self.vectors = [normalize(v, self.embedder.dimensions) for v in data["vectors"]]
                return self.vectors, "disk_hit"
            except (OSError, ValueError, KeyError, TypeError, EmbeddingError):
                cache_state = "corrupt_rebuilt"
        raw = self.embedder.embed([e["title"] + "\n" + e["text"] for e in self.entries], "document")
        if len(raw) != len(self.entries):
            raise EmbeddingError("INVALID_VECTOR", "文档向量条数与片段数不一致")
        self.vectors = [normalize(v, self.embedder.dimensions) for v in raw]
        if cache is not None:
            temporary = cache.with_suffix("." + uuid4().hex + ".tmp")
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps({"fingerprint": self.fingerprint, "vectors": self.vectors},
                                                allow_nan=False), encoding="utf-8")
                temporary.replace(cache)
            except OSError:
                # A cache problem does not invalidate vectors actually obtained from the API.
                cache_state = "write_failed_memory_only"
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return self.vectors, cache_state

    def search(self, query: str, minimum: float) -> tuple[list[tuple[float, tuple[str, str]]], dict]:
        started = len(self.embedder.requests)
        vectors, cache_state = self._documents()
        raw_query = self.embedder.embed([query], "query")
        if len(raw_query) != 1:
            raise EmbeddingError("INVALID_VECTOR", "查询应返回一个向量")
        vector = normalize(raw_query[0], self.embedder.dimensions)
        ranked = []
        for entry, document in zip(self.entries, vectors):
            score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(vector, document))))
            if score >= minimum:
                ranked.append((score, (entry["doc_id"], entry["chunk_id"])))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]))
        return ranked, {"provider": self.embedder.provider, "model": self.embedder.model,
                        "dimensions": self.embedder.dimensions, "evidence_kind": self.embedder.evidence_kind,
                        "index_fingerprint": self.fingerprint, "document_cache": cache_state,
                        "requests": self.embedder.requests[started:]}
