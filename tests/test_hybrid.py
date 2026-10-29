"""Controlled vectors test fusion/HTTP contracts; they do not measure DashScope quality."""
from __future__ import annotations

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.agent import Agent
from knowledge_agent.cli import main
from knowledge_agent.config import load_env
from knowledge_agent.embeddings import DashScopeEmbedder, EmbeddingError, normalize
from knowledge_agent.evaluation import grade
from knowledge_agent.knowledge import KnowledgeBase, ToolError
from knowledge_agent.models import MockModel
from knowledge_agent.retrieval import rrf
from test_agent import knowledge


class FixtureEmbedder:
    provider, model, evidence_kind = "fixture", "controlled-vectors", "controlled_vectors_only"
    dimensions, cache_key = 2, "controlled-vectors-v1"

    def __init__(self, query_vector=None, doc_vectors=None, failure=None):
        self.requests = []
        self.query_vector = query_vector or [1.0, 0.0]
        self.doc_vectors = doc_vectors
        self.failure = failure

    def embed(self, texts, text_type):
        if self.failure:
            raise self.failure
        self.requests.append({"text_type": text_type, "batch_size": len(texts)})
        if text_type == "query":
            return [self.query_vector]
        return self.doc_vectors or [[1.0, 0.0] if i == 0 else [0.0, 1.0] for i in range(len(texts))]


def native_response(payload):
    size = len(payload["input"]["texts"])
    dim = payload["parameters"]["dimension"]
    return {"request_id": "synthetic-request", "output": {"embeddings": [
        {"index": i, "embedding": [float(i + 1), 1.0] + [0.0] * (dim - 2)}
        for i in reversed(range(size))]}, "usage": {"total_tokens": size * 3}}


class FusionTests(unittest.TestCase):
    def test_rrf_uses_one_based_ranks_and_combines_both_channels(self):
        a, b, c = ("A", "C"), ("B", "C"), ("C", "C")
        result = rrf([[a, b, c], [b, c, a]], 60)
        self.assertEqual(result[0][1], b)
        self.assertAlmostEqual(result[0][0], 1 / 62 + 1 / 61)
        self.assertAlmostEqual(dict((key, score) for score, key in result)[a], 1 / 61 + 1 / 63)

    def test_rrf_deduplicates_a_channel_and_breaks_ties_stably(self):
        a, b = ("A", "C"), ("B", "C")
        self.assertEqual(rrf([[a, a]], 60), [(1 / 61, a)])
        self.assertEqual([key for _, key in rrf([[b], [a]])], [a, b])
        self.assertEqual(rrf([[], []]), [])
        for invalid in (0, -1, True):
            with self.assertRaises(ValueError):
                rrf([[a]], invalid)

    def test_vector_only_candidate_is_recalled_without_keyword_overlap(self):
        query = "垫的钱最迟啥时候能报账？"
        self.assertEqual(knowledge().search_docs(query, 8)["hits"], [])
        kb = KnowledgeBase(knowledge().documents, retrieval="hybrid", embedder=FixtureEmbedder())
        result = kb.search_docs(query, 8)
        self.assertEqual([h["doc_id"] for h in result["hits"]], ["D01"])
        hit = result["hits"][0]
        self.assertIsNone(hit["bm25_rank"])
        self.assertIsNone(hit["bm25_score"])
        self.assertEqual(hit["vector_rank"], 1)
        self.assertAlmostEqual(hit["score"], 1 / 61, places=7)
        self.assertEqual(result["retrieval"]["embedding"]["evidence_kind"], "controlled_vectors_only")

    def test_low_similarity_is_filtered_but_lexical_candidates_survive(self):
        embedder = FixtureEmbedder(doc_vectors=[[0.6, 0.8], [0.0, 1.0]])
        kb = KnowledgeBase(knowledge().documents, retrieval="hybrid", embedder=embedder, vector_min_score=0.9)
        self.assertEqual(kb.search_docs("垫的钱最迟啥时候能报账？", 8)["hits"], [])
        found = kb.search_docs("报销期限", 8)
        self.assertIn("D01", {h["doc_id"] for h in found["hits"]})
        self.assertTrue(all(h["vector_rank"] is None for h in found["hits"]))

    def test_fusion_reports_both_scores_and_truncation(self):
        kb = KnowledgeBase(knowledge().documents, retrieval="hybrid", embedder=FixtureEmbedder())
        result = kb.search_docs("报销", 1)
        self.assertTrue(result["has_more"])
        hit = result["hits"][0]
        expected = sum(1 / (60 + rank) for rank in (hit["bm25_rank"], hit["vector_rank"]) if rank is not None)
        self.assertAlmostEqual(hit["score"], expected, places=7)
        self.assertEqual(hit["score_type"], "rrf")

    def test_candidate_pool_truncation_remains_visible_even_if_top_k_is_full(self):
        document = knowledge().documents[0]
        documents = [copy.deepcopy(document) | {"doc_id": f"D{i:02d}"} for i in range(12)]
        client = FixtureEmbedder(doc_vectors=[[1.0, 0.0]] * 12)
        kb = KnowledgeBase(documents, retrieval="hybrid", embedder=client, candidate_k=8)
        result = kb.search_docs("报销期限", 8)
        self.assertEqual(len(result["hits"]), 8)
        self.assertTrue(result["has_more"])
        self.assertTrue(result["retrieval"]["candidate_pool_truncated"])
        self.assertEqual(result["retrieval"]["bm25_matches"], 12)
        self.assertEqual(result["retrieval"]["vector_matches"], 12)

    def test_missing_key_explicitly_degrades_and_strict_mode_fails(self):
        options = {"retrieval": "hybrid", "embedder": DashScopeEmbedder("")}
        result = KnowledgeBase(knowledge().documents, **options).search_docs("报销期限", 8)
        self.assertEqual(result["retrieval"]["used_mode"], "bm25")
        self.assertTrue(result["retrieval"]["degraded"])
        self.assertEqual(result["retrieval"]["warning"]["code"], "CONFIG_MISSING")
        with self.assertRaises(ToolError) as caught:
            KnowledgeBase(knowledge().documents, **options, require_vector=True).search_docs("报销", 8)
        self.assertEqual(caught.exception.code, "EMBEDDING_UNAVAILABLE")

    def test_embedding_failure_never_becomes_silent_hybrid_success(self):
        for code in ("HTTP_401", "NETWORK_ERROR", "PROTOCOL_ERROR"):
            with self.subTest(code=code):
                client = FixtureEmbedder(failure=EmbeddingError(code, "安全错误摘要"))
                result = KnowledgeBase(knowledge().documents, retrieval="hybrid", embedder=client).search_docs("报销", 8)
                self.assertTrue(result["retrieval"]["degraded"])
                self.assertEqual(result["retrieval"]["warning"]["code"], code)

    def test_document_cache_reuses_vectors_and_invalidates_on_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            options = {"retrieval": "hybrid", "cache_dir": Path(directory)}
            first = FixtureEmbedder()
            kb = KnowledgeBase(knowledge().documents, embedder=first, **options)
            initial = kb.search_docs("报销", 8)
            again = kb.search_docs("报销期限", 8)
            self.assertEqual(initial["retrieval"]["embedding"]["document_cache"], "built")
            self.assertEqual(again["retrieval"]["embedding"]["document_cache"], "memory_hit")
            self.assertEqual(sum(r["text_type"] == "document" for r in first.requests), 1)
            second = FixtureEmbedder()
            loaded = KnowledgeBase(knowledge().documents, embedder=second, **options).search_docs("报销", 8)
            self.assertEqual(loaded["retrieval"]["embedding"]["document_cache"], "disk_hit")
            self.assertFalse(any(r["text_type"] == "document" for r in second.requests))
            changed = knowledge().documents
            changed[0]["chunks"][0]["text"] = "报销期限为 45 天。"
            updated = KnowledgeBase(changed, embedder=FixtureEmbedder(), **options).search_docs("报销", 8)
            self.assertNotEqual(initial["retrieval"]["embedding"]["index_fingerprint"], updated["retrieval"]["embedding"]["index_fingerprint"])
            self.assertEqual(updated["retrieval"]["embedding"]["document_cache"], "built")

    def test_corrupt_cache_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            options = {"retrieval": "hybrid", "cache_dir": Path(directory)}
            kb = KnowledgeBase(knowledge().documents, embedder=FixtureEmbedder(), **options)
            kb.search_docs("报销", 8)
            cache = next(Path(directory).glob("*.json"))
            cache.write_text('{"broken": true}', encoding="utf-8")
            rebuilt = KnowledgeBase(knowledge().documents, embedder=FixtureEmbedder(), **options).search_docs("报销", 8)
            self.assertEqual(rebuilt["retrieval"]["embedding"]["document_cache"], "corrupt_rebuilt")

    def test_model_observes_degradation_and_evaluation_does_not_count_it_as_hybrid(self):
        kb = KnowledgeBase(knowledge().documents, retrieval="hybrid", embedder=DashScopeEmbedder(""))
        record = Agent(MockModel(), kb).run("报销期限多久？")
        checks = grade(record, {"status": "answered", "citations": ["D01"]}, kb)
        self.assertTrue(checks["status"])
        self.assertFalse(checks["hybrid_not_degraded"])
        self.assertIn("降级", record["trace"][0]["observation_summary"])


class DashScopeTests(unittest.TestCase):
    def test_batches_of_ten_reordering_and_text_types(self):
        calls = []

        def transport(payload):
            calls.append(payload)
            return native_response(payload)

        client = DashScopeEmbedder("synthetic-key", dimensions=64, transport=transport)
        vectors = client.embed(["text"] * 11, "document")
        self.assertEqual(len(vectors), 11)
        self.assertEqual([len(p["input"]["texts"]) for p in calls], [10, 1])
        self.assertEqual(calls[0]["parameters"], {"text_type": "document", "dimension": 64, "output_type": "dense"})
        self.assertEqual(calls[0]["model"], "text-embedding-v4")
        self.assertAlmostEqual(vectors[0][0], 1 / math.sqrt(2))
        self.assertAlmostEqual(vectors[1][0], 2 / math.sqrt(5))
        client.embed(["query"], "query")
        self.assertEqual(calls[-1]["parameters"]["text_type"], "query")
        self.assertEqual(client.requests[-1]["total_tokens"], 3)

    def test_legacy_text_index_is_supported(self):
        def transport(payload):
            response = native_response(payload)
            for item in response["output"]["embeddings"]:
                item["text_index"] = item.pop("index")
            return response
        self.assertEqual(len(DashScopeEmbedder("synthetic", dimensions=64, transport=transport).embed(["a"], "query")), 1)

    def test_malformed_vectors_and_indices_are_rejected(self):
        payload = {"input": {"texts": ["a", "b"]}, "parameters": {"dimension": 64}}
        for flaw in ("count", "duplicate", "missing", "dim", "zero", "nan", "error_body"):
            with self.subTest(flaw=flaw):
                response = native_response(payload)
                items = response["output"]["embeddings"]
                if flaw == "count": items.pop()
                elif flaw == "duplicate": items[1]["index"] = items[0]["index"]
                elif flaw == "missing": items[0].pop("index")
                elif flaw == "dim": items[0]["embedding"] = [1.0]
                elif flaw == "zero": items[0]["embedding"] = [0.0] * 64
                elif flaw == "nan": items[0]["embedding"][0] = float("nan")
                else: response = {"code": "InvalidApiKey", "message": "SECRET"}
                client = DashScopeEmbedder("synthetic", dimensions=64, transport=lambda p: response)
                with self.assertRaises(EmbeddingError) as caught:
                    client.embed(["a", "b"], "document")
                self.assertNotIn("SECRET", str(caught.exception))

    def test_http_contract_without_a_real_dashscope_call(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, self.headers["Authorization"], payload))
                body = json.dumps(native_response(payload)).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = DashScopeEmbedder("local-fake-key", dimensions=64,
                                      url=f"http://127.0.0.1:{server.server_port}/embeddings")
            vectors = client.embed(["测试查询"], "query")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(len(vectors[0]), 64)
        self.assertEqual(captured[0][0:2], ("/embeddings", "Bearer local-fake-key"))
        self.assertNotIn("local-fake-key", json.dumps(client.requests))

    def test_network_errors_are_safe_and_missing_key_never_sends_a_request(self):
        for error in (HTTPError("https://example.test", 401, "SECRET", {}, io.BytesIO(b"SECRET")),
                      URLError("SECRET"), TimeoutError("SECRET")):
            with self.subTest(error=type(error).__name__), patch("knowledge_agent.embeddings.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(EmbeddingError) as caught:
                    DashScopeEmbedder("SECRET").embed(["text"], "query")
                self.assertNotIn("SECRET", str(caught.exception))
        transport = unittest.mock.Mock()
        with self.assertRaises(EmbeddingError):
            DashScopeEmbedder("", transport=transport).embed(["text"], "query")
        transport.assert_not_called()

    def test_configuration_and_cache_identity(self):
        for options in ({"url": "http://remote.test"}, {"url": "https://user:secret@example.test"},
                        {"url": "https://example.test?api_key=secret"}, {"dimensions": 2},
                        {"timeout": 0}, {"model": "embedding-v4"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                DashScopeEmbedder(**options)
        self.assertNotEqual(DashScopeEmbedder(dimensions=64).cache_key, DashScopeEmbedder(dimensions=128).cache_key)
        self.assertEqual(DashScopeEmbedder("key-a").cache_key, DashScopeEmbedder("key-b").cache_key)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text("DASHSCOPE_API_KEY=synthetic-only\nDASHSCOPE_EMBEDDING_MODEL=text-embedding-v4\n"
                            "DASHSCOPE_EMBEDDING_DIMENSIONS=64\nDASHSCOPE_TIMEOUT=5\n", encoding="utf-8")
            load_env(path)
            configured = DashScopeEmbedder.from_env()
            self.assertEqual(configured.model, "text-embedding-v4")
            self.assertEqual(configured.dimensions, 64)
            self.assertEqual(configured.timeout, 5)

    def test_cli_hybrid_without_key_is_explicit_and_strict_mode_is_nonzero(self):
        for strict in (False, True):
            with self.subTest(strict=strict), patch.dict(os.environ, {}, clear=True), patch("knowledge_agent.cli.load_env"):
                output = io.StringIO()
                with patch("sys.stdout", output), patch("sys.stderr", io.StringIO()):
                    code = main(["ask", "报销期限多久？", "--provider", "mock", "--retrieval", "hybrid"]
                                + (["--require-vector"] if strict else []))
                record = json.loads(output.getvalue())
                if strict:
                    self.assertEqual(code, 2)
                    self.assertEqual(record["result"]["status"], "tool_error")
                else:
                    self.assertEqual(code, 0)
                    self.assertTrue(record["trace"][0]["observation"]["retrieval"]["degraded"])


if __name__ == "__main__":
    unittest.main()
