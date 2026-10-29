"""Local BM25 retrieval and exact chunk reads. No model-dependent search branch."""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re


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
    def __init__(self, documents: list[dict], faults: dict[str, int] | None = None):
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

    @classmethod
    def from_path(cls, path: str | Path, faults: dict | None = None) -> "KnowledgeBase":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls(data["documents"], faults)

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
        hits = []
        for score, entry in ranked[:top_k]:
            hits.append({key: value for key, value in entry.items() if key != "text"} |
                        {"score": round(score, 4), "preview": entry["text"][:80]})
        return {"ok": True, "query": query, "hits": hits,
                "total_matches": len(ranked), "has_more": len(ranked) > top_k}

    def read_chunk(self, doc_id: str, chunk_id: str) -> dict:
        self._check_fault("read_chunk")
        for entry in self.entries:
            if (entry["doc_id"], entry["chunk_id"]) == (doc_id, chunk_id):
                return {"ok": True, "chunk": dict(entry)}
        raise ToolError("NOT_FOUND", "文档或片段不存在")
