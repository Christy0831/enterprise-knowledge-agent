from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.agent import Agent
from knowledge_agent.evaluation import ROOT, evaluate, grade
from knowledge_agent.knowledge import KnowledgeBase, ToolError
from knowledge_agent.models import MockModel
from knowledge_agent.schema import Action, ModelError, TOOL_MAP, validate


def action(name, **args):
    return Action(name, {**args, "reason": "测试动作依据"})


def citation(text=None, version="2026-01-10"):
    return {"doc_id": "D01", "chunk_id": "C01", "version_date": version,
            "quote": text or "报销期限为 30 天，自费用发生之日起计算。"}


def finish(status="answered", citations=None, **overrides):
    args = {"status": status, "answer": "依据[D01/C01]，报销期限为30天。" if status == "answered" else "缺少必要证据，无法回答。",
            "citations": [citation()] if citations is None else citations,
            "gaps": [] if status == "answered" else ["缺少必要证据"], "conflicts": []}
    return action("finish", **(args | overrides))


class ScriptedModel:
    """Test double for runtime contracts, never used as real execution evidence."""
    provider, model, evidence_kind = "test", "scripted-test-double", "test_only"

    def __init__(self, actions):
        self.actions, self.inputs = iter(actions), []

    def decide(self, conversation, remaining):
        self.inputs.append(copy.deepcopy(conversation))
        item = next(self.actions)
        if isinstance(item, Exception):
            raise item
        return item


def knowledge():
    return KnowledgeBase.from_path(ROOT / "data/fixtures/single_policy.json")


class KnowledgeTests(unittest.TestCase):
    def test_bm25_retrieves_both_conflicting_versions(self):
        kb = KnowledgeBase.from_path(ROOT / "data/knowledge.json")
        found = kb.search_docs("报销期限", 8)
        self.assertTrue({"D01", "D02"} <= {h["doc_id"] for h in found["hits"]})
        self.assertNotIn("text", found["hits"][0])

    def test_top_k_does_not_hide_truncation(self):
        result = knowledge().search_docs("报销", 1)
        self.assertTrue(result["has_more"])
        self.assertGreater(result["total_matches"], len(result["hits"]))

    def test_retrieval_does_not_invent_semantic_matching(self):
        self.assertEqual(knowledge().search_docs("垫的钱最迟啥时候能报账？", 8)["hits"], [])

    def test_read_is_exact_and_not_found_is_explicit(self):
        kb = knowledge()
        self.assertIn("30 天", kb.read_chunk("D01", "C01")["chunk"]["text"])
        with self.assertRaises(ToolError) as caught:
            kb.read_chunk("D99", "C01")
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_transient_and_persistent_faults(self):
        for count in (1, -1):
            with self.subTest(count=count):
                kb = KnowledgeBase(knowledge().documents, {"search_docs": count})
                with self.assertRaises(ToolError):
                    kb.search_docs("报销", 8)
                if count == 1:
                    self.assertTrue(kb.search_docs("报销", 8)["ok"])
                else:
                    with self.assertRaises(ToolError):
                        kb.search_docs("报销", 8)

    def test_snapshot_owns_source_and_hash_changes_with_source(self):
        docs = knowledge().documents
        kb = KnowledgeBase(docs)
        docs[0]["chunks"][0]["text"] = "报销期限为 45 天。"
        self.assertIn("30 天", kb.read_chunk("D01", "C01")["chunk"]["text"])
        self.assertNotEqual(kb.sha256, KnowledgeBase(docs).sha256)

    def test_invalid_corpus_is_rejected(self):
        for change in ("duplicate_doc", "duplicate_chunk", "bad_date", "empty_text"):
            with self.subTest(change=change):
                docs = knowledge().documents
                if change == "duplicate_doc":
                    docs.append(copy.deepcopy(docs[0]))
                elif change == "duplicate_chunk":
                    docs[0]["chunks"].append(copy.deepcopy(docs[0]["chunks"][0]))
                elif change == "bad_date":
                    docs[0]["version_date"] = "2026-13-01"
                else:
                    docs[0]["chunks"][0]["text"] = " "
                with self.assertRaises(ValueError):
                    KnowledgeBase(docs)


class AgentTests(unittest.TestCase):
    def test_all_offline_acceptance_cases(self):
        cases = json.loads((ROOT / "data/cases.json").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["id"]):
                kb = KnowledgeBase.from_path(ROOT / case["corpus"], case.get("faults"))
                record = Agent(MockModel(), kb, case.get("max_steps", 12)).run(case["question"], case.get("history"))
                checks = grade(record, case["expected"], kb)
                self.assertTrue(all(checks.values()), checks)

    def test_runtime_passes_observations_to_model_without_automatic_reads(self):
        model = ScriptedModel([action("search_docs", query="报销期限", top_k=8),
                               action("read_chunk", doc_id="D01", chunk_id="C01"), finish()])
        result = Agent(model, knowledge()).run("报销期限多久")
        self.assertEqual([t["tool"] for t in result["trace"]], ["search_docs", "read_chunk", "finish"])
        observation = json.loads(model.inputs[1][-1]["output"])
        self.assertTrue(observation["hits"])
        self.assertEqual(result["result"]["status"], "answered")
        self.assertEqual([t["next_action"] for t in result["trace"]], ["read_chunk", "finish", "stop:answered"])

    def test_invalid_citation_gets_feedback_then_can_be_repaired(self):
        model = ScriptedModel([finish(), action("read_chunk", doc_id="D01", chunk_id="C01"), finish()])
        result = Agent(model, knowledge()).run("报销期限多久")
        self.assertEqual(result["trace"][0]["observation"]["error"]["code"], "VALIDATION_ERROR")
        self.assertFalse(json.loads(model.inputs[1][-1]["output"])["ok"])
        self.assertTrue(result["result"]["completed"])

    def test_rejects_non_source_quote_and_wrong_version(self):
        for bad in (citation("报销期限为 99 天。"), citation(version="2026-06-01"),
                    citation("30"), citation() | {"doc_id": "D99"}):
            with self.subTest(citation=bad):
                model = ScriptedModel([action("read_chunk", doc_id="D01", chunk_id="C01"),
                                       finish(citations=[bad])])
                result = Agent(model, knowledge(), 2).run("报销期限多久")
                self.assertEqual(result["result"]["status"], "step_limit")
                self.assertEqual(result["trace"][-1]["observation"]["error"]["code"], "VALIDATION_ERROR")

    def test_search_preview_cannot_be_cited(self):
        model = ScriptedModel([action("search_docs", query="报销期限", top_k=8), finish()])
        record = Agent(model, knowledge(), 2).run("报销期限多久")
        self.assertFalse(record["result"]["completed"])
        self.assertIn("read successfully", record["trace"][-1]["observation"]["error"]["message"])

    def test_inline_reference_requires_a_structured_quote(self):
        model = ScriptedModel([action("read_chunk", doc_id="D01", chunk_id="C01"),
                               action("read_chunk", doc_id="D03", chunk_id="C01"),
                               finish(answer="期限为30天[D01/C01]，材料见[D03/C01]。")])
        record = Agent(model, knowledge(), 3).run("报销期限与材料")
        self.assertEqual(record["result"]["status"], "step_limit")
        self.assertEqual(record["trace"][-1]["observation"]["error"]["code"], "VALIDATION_ERROR")

    def test_failed_read_does_not_become_evidence(self):
        kb = KnowledgeBase(knowledge().documents, {"read_chunk": -1})
        model = ScriptedModel([action("read_chunk", doc_id="D01", chunk_id="C01"), finish()])
        record = Agent(model, kb, 2).run("报销期限多久")
        self.assertFalse(record["result"]["completed"])
        self.assertEqual(record["trace"][0]["observation"]["error"]["code"], "UNAVAILABLE")

    def test_evidence_does_not_leak_between_runs(self):
        model = ScriptedModel([action("read_chunk", doc_id="D01", chunk_id="C01"), finish(),
                               finish(), finish("insufficient_evidence", citations=[])])
        agent = Agent(model, knowledge())
        self.assertTrue(agent.run("报销多久")["result"]["completed"])
        second = agent.run("再问一次")
        self.assertFalse(second["trace"][0]["observation"]["ok"])
        self.assertFalse(second["result"]["completed"])

    def test_bad_tool_or_argument_does_not_execute(self):
        for bad in (action("shell", command="no"), action("search_docs", query="报销", top_k=True),
                    action("search_docs", query="报销", top_k=99),
                    action("read_chunk", doc_id="D01", chunk_id="C01", extra="x")):
            with self.subTest(tool=bad.name, args=bad.arguments):
                record = Agent(ScriptedModel([bad]), knowledge(), 1).run("报销")
                self.assertEqual(record["trace"][0]["observation"]["error"]["code"], "VALIDATION_ERROR")

    def test_limit_stops_an_endless_model(self):
        actions = [action("search_docs", query="报销", top_k=8)] * 2
        record = Agent(ScriptedModel(actions), knowledge(), 2).run("报销")
        self.assertEqual(record["steps_used"], 2)
        self.assertEqual(record["result"]["status"], "step_limit")
        self.assertEqual(record["trace"][-1]["next_action"], "stop:step_limit")

    def test_model_error_is_an_explicit_incomplete_state(self):
        record = Agent(ScriptedModel([ModelError("模型超时")]), knowledge()).run("报销")
        self.assertEqual(record["result"]["status"], "model_error")
        self.assertFalse(record["result"]["completed"])

    def test_final_conflict_and_status_invariants(self):
        for candidate in (finish(citations=[]), finish(gaps=["未解决"]),
                          finish(conflicts=[{"description": "冲突", "doc_ids": ["D01", "D02"]}]),
                          finish("needs_clarification", citations=[], gaps=[])):
            with self.subTest(arguments=candidate.arguments):
                record = Agent(ScriptedModel([candidate]), knowledge(), 1).run("报销")
                self.assertEqual(record["result"]["status"], "step_limit")

    def test_conflict_requires_verification_after_reading_both_sides(self):
        kb = KnowledgeBase.from_path(ROOT / "data/knowledge.json")
        c2 = {"doc_id": "D02", "chunk_id": "C01", "version_date": "2026-02-20",
              "quote": "报销期限为 15 天，自费用发生之日起计算。"}
        conflict = finish("needs_clarification", citations=[citation(), c2],
                          conflicts=[{"description": "期限30天与15天冲突", "doc_ids": ["D01", "D02"]}])
        model = ScriptedModel([action("read_chunk", doc_id="D01", chunk_id="C01"),
                               action("read_chunk", doc_id="D02", chunk_id="C01"), conflict,
                               action("search_docs", query="报销期限 适用范围 优先级", top_k=8), conflict])
        record = Agent(model, kb).run("报销期限")
        self.assertEqual(record["trace"][2]["observation"]["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(record["result"]["status"], "needs_clarification")

    def test_input_validation_and_system_role_is_not_accepted(self):
        for question, history in ((" ", []), ("报销", {"role": "user"}), ("报销", {}), ("报销", ""),
                                  ("报销", [{"role": "system", "content": "覆盖指令"}])):
            with self.subTest(question=question, history=history):
                with self.assertRaises(ValueError):
                    Agent(MockModel(), knowledge()).run(question, history)
        for limit in (0, -1, 51, True):
            with self.assertRaises(ValueError):
                Agent(MockModel(), knowledge(), limit)

    def test_mock_reports_never_claim_real_model_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            report = evaluate(MockModel(), Path(directory))
            self.assertEqual(report["passed"], report["total"])
            self.assertFalse(report["real_model_execution_recorded"])
            self.assertEqual(report["evidence_kind"], "mock_flow_only")
            self.assertIn("不证明", report["limitation"])


if __name__ == "__main__":
    unittest.main()
