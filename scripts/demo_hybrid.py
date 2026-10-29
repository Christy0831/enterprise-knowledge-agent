"""Generate honest, reproducible RRF fixtures without calling any embedding API."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_agent.agent import Agent
from knowledge_agent.embeddings import DashScopeEmbedder
from knowledge_agent.evaluation import write_json
from knowledge_agent.knowledge import KnowledgeBase
from knowledge_agent.models import MockModel


class ControlledVectors:
    provider, model = "fixture", "controlled-vectors"
    evidence_kind = "controlled_vectors_only"
    dimensions, cache_key = 2, "hybrid-demo-fixture-v1"

    def __init__(self):
        self.requests = []

    def embed(self, texts, text_type):
        self.requests.append({"request_id": "fixture-only", "text_type": text_type, "batch_size": len(texts)})
        if text_type == "query":
            return [[1.0, 0.0]]
        # Known vectors test rank fusion; this is not a trained semantic model.
        return [[1.0, 0.0] if "期限" in text else [0.0, 1.0] for text in texts]


def main():
    out = ROOT / "examples/hybrid"
    source = ROOT / "data/fixtures/single_policy.json"
    for filename, query in (("01_rrf_consensus", "报销期限"), ("02_vector_only_fixture", "垫的钱最迟啥时候能报账？")):
        bm25 = KnowledgeBase.from_path(source).search_docs(query, 8)
        hybrid = KnowledgeBase.from_path(source, retrieval="hybrid", embedder=ControlledVectors()).search_docs(query, 8)
        first = hybrid["hits"][0]
        expected_rrf = sum(1 / (60 + rank) for rank in (first["bm25_rank"], first["vector_rank"]) if rank is not None)
        assert abs(first["score"] - expected_rrf) < 1e-7
        if filename == "02_vector_only_fixture":
            assert not bm25["hits"] and first["bm25_rank"] is None and first["doc_id"] == "D01"
        write_json(out / (filename + ".json"), {
            "evidence_kind": "controlled_vectors_only", "command": "python scripts/demo_hybrid.py",
            "limitation": "人工指定向量，仅证明融合和候选合并流程，不证明DashScope真实语义召回或质量提升。",
            "input": {"query": query}, "bm25": bm25, "hybrid": hybrid,
            "checks": {"rrf_formula": True, "candidate_recalled": first["doc_id"] == "D01"}})
    for strict, filename in ((False, "03_missing_key_degradation"), (True, "04_missing_key_strict_failure")):
        kb = KnowledgeBase.from_path(source, retrieval="hybrid", embedder=DashScopeEmbedder(""), require_vector=strict)
        record = Agent(MockModel(), kb).run("报销期限多久？")
        assert record["result"]["status"] == ("tool_error" if strict else "answered")
        record["embedding_evidence_kind"] = "no_embedding_call_missing_key"
        record["command"] = "python scripts/demo_hybrid.py"
        write_json(out / (filename + ".json"), record)
    print("Generated 4 hybrid flow records; no real DashScope calls or semantic-quality claims.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
