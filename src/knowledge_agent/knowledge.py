"""BM25 + optional DashScope dense retrieval, fused with reciprocal ranks."""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from .embeddings import EmbeddingError
from .retrieval import DenseIndex, rrf


STOP = {"什么", "怎么", "多少", "如何", "一下", "是否", "可以", "需要", "我们", "你们"}


def tokenize(text: str) -> list[str]:
    tokens = []
    for segment in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", segment):
            tokens.extend(segment[i:i + 2] for i in range(len(segment) - 1)
                          if segment[i:i + 2] not in STOP)
        else:
            tokens.append(segment)
    return tokens


class ToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable

    def observation(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": str(self),
                                       "retryable": self.retryable}}


class KnowledgeBase:
    def __init__(self, documents: list[dict], faults: dict[str, int] | None = None, *,
                 retrieval: str = "bm25", embedder=None, cache_dir: Path | None = None,
                 rrf_k: int = 60, candidate_k: int = 20, vector_min_score: float = 0.5,
                 require_vector: bool = False):
        if retrieval not in {"bm25", "hybrid"}:
            raise ValueError("retrieval must be bm25 or hybrid")
        if type(rrf_k) is not int or rrf_k < 1 or type(candidate_k) is not int or candidate_k < 8:
            raise ValueError("rrf_k须为正整数，candidate_k须为至少8的整数")
        if not math.isfinite(vector_min_score) or not -1 <= vector_min_score <= 1:
            raise ValueError("vector_min_score须在-1到1之间")
        if retrieval == "hybrid" and embedder is None:
            raise ValueError("hybrid模式需要embedding适配器")
        self.retrieval, self.embedder = retrieval, embedder
        self.rrf_k, self.candidate_k = rrf_k, candidate_k
        self.vector_min_score, self.require_vector = vector_min_score, require_vector
        # Own our snapshot, so source changes cannot invalidate evidence mid-run.
        self.documents = json.loads(json.dumps(documents))
        self.faults = dict(faults or {})
        self.entries = []
        seen = set()
        for doc in self.documents:
            doc_id = doc["doc_id"]
            if not isinstance(doc_id, str) or not doc_id or doc_id in seen:
                raise ValueError("Document IDs must be nonempty and unique")
            seen.add(doc_id)
            date.fromisoformat(doc["version_date"])
            if not isinstance(doc["title"], str) or not doc["title"].strip():
                raise ValueError("Document title must be nonempty")
            if not doc["chunks"]:
                raise ValueError("Document must contain a chunk")
            chunk_ids = set()
            for chunk in doc["chunks"]:
                chunk_id, content = chunk["chunk_id"], chunk["text"]
                if not isinstance(chunk_id, str) or not chunk_id or chunk_id in chunk_ids:
                    raise ValueError("Chunk IDs must be nonempty and unique within document")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Chunk text must be nonempty")
                chunk_ids.add(chunk_id)
                metadata = {key: doc.get(key) for key in
                            ("doc_id", "title", "version_date", "scope", "priority")}
                self.entries.append({**metadata, "chunk_id": chunk_id, "text": content})
        if not self.entries:
            raise ValueError("Knowledge base must not be empty")
        self.counters = [Counter(tokenize(e["title"] + " " + e["text"])) for e in self.entries]
        self.df = Counter(token for counter in self.counters for token in counter)
        self.avg_length = sum(sum(c.values()) for c in self.counters) / len(self.counters)
        snapshot = json.dumps(self.documents, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.sha256 = hashlib.sha256(snapshot).hexdigest()
        self.dense_index = DenseIndex(self.entries, self.sha256, embedder, cache_dir) if retrieval == "hybrid" else None

    @classmethod
    def from_path(cls, path: str | Path, faults: dict | None = None, **options) -> "KnowledgeBase":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls(data["documents"], faults, **options)

    def _check_fault(self, name: str) -> None:
        remaining = self.faults.get(name, 0)
        if remaining:
            if remaining > 0:
                self.faults[name] -= 1
            raise ToolError("UNAVAILABLE", f"{name}: 合成故障注入，工具暂不可用", retryable=True)

    def search_docs(self, query: str, top_k: int) -> dict:
        self._check_fault("search_docs")
        if not query.strip():
            raise ToolError("INVALID_ARGUMENT", "查询不能为空")
        if type(top_k) is not int or not 1 <= top_k <= 8:
            raise ToolError("INVALID_ARGUMENT", "top_k须为1到8的整数")
        query_tokens = set(tokenize(query))
        ranked = []
        n = len(self.entries)
        for entry, counter in zip(self.entries, self.counters):
            score = 0.0
            length = sum(counter.values())
            for term in query_tokens & counter.keys():
                frequency = counter[term]
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                score += idf * frequency * 2.5 / (
                    frequency + 1.5 * (0.25 + 0.75 * length / self.avg_length))
            if score > 0:
                ranked.append((score, entry))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["doc_id"], pair[1]["chunk_id"]))
        lexical = [(score, (entry["doc_id"], entry["chunk_id"])) for score, entry in ranked]
        lexical_scores = {key: score for score, key in lexical}
        vector_scores, vector_ranks = {}, {}
        lexical_ranks = {key: rank for rank, (_, key) in enumerate(lexical[:self.candidate_k], 1)}
        metadata = {"requested_mode": self.retrieval, "used_mode": "bm25", "degraded": False,
                    "score_type": "bm25", "bm25_matches": len(lexical)}
        fused = lexical
        if self.dense_index is not None:
            request_start = len(self.embedder.requests)
            try:
                dense, embedding = self.dense_index.search(query, self.vector_min_score)
                vector_scores = {key: score for score, key in dense}
                vector_ranks = {key: rank for rank, (_, key) in enumerate(dense[:self.candidate_k], 1)}
                fused = rrf([list(lexical_ranks), list(vector_ranks)], self.rrf_k)
                metadata.update({"used_mode": "hybrid", "score_type": "rrf", "rrf_k": self.rrf_k,
                                 "candidate_k": self.candidate_k, "vector_min_score": self.vector_min_score,
                                 "vector_matches": len(dense), "embedding": embedding,
                                 "candidate_pool_truncated": len(lexical) > self.candidate_k or len(dense) > self.candidate_k})
            except EmbeddingError as exc:
                if self.require_vector:
                    raise ToolError("EMBEDDING_UNAVAILABLE", str(exc), exc.retryable) from None
                metadata.update({"degraded": True, "warning": exc.safe_dict(),
                                 "embedding": {"provider": self.embedder.provider, "model": self.embedder.model,
                                               "evidence_kind": self.embedder.evidence_kind,
                                               "requests": self.embedder.requests[request_start:]}})
        entries = {(e["doc_id"], e["chunk_id"]): e for e in self.entries}
        hits = []
        for score, key in fused[:top_k]:
            entry = entries[key]
            hits.append({key: value for key, value in entry.items() if key != "text"} |
                        {"score": round(score, 8), "score_type": metadata["score_type"],
                         "bm25_score": round(lexical_scores[key], 6) if key in lexical_scores else None,
                         "bm25_rank": lexical_ranks.get(key), "vector_rank": vector_ranks.get(key),
                         "vector_score": round(vector_scores[key], 6) if key in vector_scores else None,
                         "preview": entry["text"][:80]})
        return {"ok": True, "query": query, "hits": hits,
                "total_matches": len(fused),
                "has_more": len(fused) > top_k or metadata.get("candidate_pool_truncated", False),
                "retrieval": metadata}

    def read_chunk(self, doc_id: str, chunk_id: str) -> dict:
        self._check_fault("read_chunk")
        for entry in self.entries:
            if (entry["doc_id"], entry["chunk_id"]) == (doc_id, chunk_id):
                return {"ok": True, "chunk": dict(entry)}
        raise ToolError("NOT_FOUND", "文档或片段不存在")
