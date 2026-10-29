"""Run acceptance cases and persist actual input/output/tool traces, not canned logs."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from .agent import Agent
from .knowledge import KnowledgeBase


ROOT = Path(__file__).resolve().parents[2]


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def grade(record: dict, expected: dict, knowledge: KnowledgeBase) -> dict[str, bool]:
    result = record["result"]
    cited_ids = {c["doc_id"] for c in result["citations"]}
    checks = {"status": result["status"] == expected["status"],
              "completed_flag": result["completed"] == (result["status"] == "answered"),
              "budget_respected": record["steps_used"] <= record["limits"]["max_steps"],
              "required_citations": set(expected.get("citations", [])) <= cited_ids,
              "answer_fragments": all(s in result["answer"] for s in expected.get("contains", [])),
              "forbidden_fragments_absent": all(s not in result["answer"] for s in expected.get("not_contains", [])),
              "explicit_gaps_if_incomplete": result["completed"] or bool(result["gaps"])}
    sources = {(e["doc_id"], e["chunk_id"]): e for e in knowledge.entries}
    checks["exact_source_quotes"] = all(
        (source := sources.get((c["doc_id"], c["chunk_id"]))) is not None
        and c["quote"] in source["text"] and c["version_date"] == source["version_date"]
        for c in result["citations"])
    read_keys = {(t["observation"]["chunk"]["doc_id"], t["observation"]["chunk"]["chunk_id"])
                 for t in record["trace"] if t["observation"].get("ok") and "chunk" in t["observation"]}
    checks["citations_actually_read"] = all((c["doc_id"], c["chunk_id"]) in read_keys for c in result["citations"])
    if expected.get("exact_citations"):
        checks["exact_citation_set"] = cited_ids == set(expected["citations"])
    if expected.get("conflict"):
        checks["conflict_reported"] = bool(result["conflicts"])
    counts = Counter(t["tool"] for t in record["trace"])
    for name, minimum in expected.get("min_tools", {}).items():
        checks["tool_count_" + name] = counts[name] >= minimum
    if expected.get("different_queries"):
        queries = {t["arguments"].get("query") for t in record["trace"] if t["tool"] == "search_docs"}
        checks["query_changed"] = len(queries) >= 2
    if expected.get("query_reformulated"):
        original = record["input"]["question"].strip("？?。 ")
        checks["query_reformulated"] = any(
            t["arguments"].get("query", "").strip("？?。 ") != original
            for t in record["trace"] if t["tool"] == "search_docs")
    if expected.get("tool_error_observed"):
        checks["fault_was_observed"] = any(
            t["observation"].get("error", {}).get("code") == "UNAVAILABLE" for t in record["trace"])
    if knowledge.retrieval == "hybrid":
        searches = [t["observation"] for t in record["trace"]
                    if t["tool"] == "search_docs" and t["observation"].get("ok")]
        checks["hybrid_not_degraded"] = all(
            s.get("retrieval", {}).get("used_mode") == "hybrid" and not s["retrieval"].get("degraded")
            for s in searches)
    return checks


def evaluate(model, out_dir: Path, cases_path: Path | None = None, *, retrieval_options: dict | None = None) -> dict:
    manifest = json.loads((cases_path or ROOT / "data/cases.json").read_text(encoding="utf-8-sig"))
    rows = []
    successful_model_steps = 0
    # Separate provider dirs so offline results never overwrite real-model evidence.
    for case in manifest["cases"]:
        knowledge = KnowledgeBase.from_path(ROOT / case["corpus"], case.get("faults"), **(retrieval_options or {}))
        record = Agent(model, knowledge, case.get("max_steps", 12)).run(case["question"], case.get("history"))
        record["fixture"] = {"corpus": case["corpus"], "faults": case.get("faults", {})}
        successful_model_steps += sum(bool(t.get("model_metadata", {}).get("response_id"))
                                      for t in record["trace"])
        checks = grade(record, case["expected"], knowledge)
        record["evaluation"] = {"expected": case["expected"], "checks": checks, "passed": all(checks.values())}
        filename = case["id"] + ".json"
        write_json(out_dir / filename, record)
        rows.append({"case_id": case["id"], "acceptance": case["acceptance"],
                     "status": record["result"]["status"], "passed": all(checks.values()),
                     "failed_checks": [k for k, v in checks.items() if not v], "record": filename,
                     "path": [t["tool"] for t in record["trace"]]})
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "provider": model.provider, "model": model.model, "evidence_kind": model.evidence_kind,
              "retrieval_mode": knowledge.retrieval,
              "embedding_evidence_kind": knowledge.embedder.evidence_kind if knowledge.embedder else None,
              "real_model_execution_recorded": model.evidence_kind == "real_model" and successful_model_steps > 0,
              "successful_model_steps": successful_model_steps,
              "total": len(rows), "passed": sum(r["passed"] for r in rows), "cases": rows,
              "limitation": "自动断言只验证状态、关键事实、引用与动作特征；需人工复核答案蕴含与决策质量。"
                            if model.provider != "mock" else
                            "仅证明离线流程；规则Mock不证明真实模型决策效果，不能据此认定ReAct验收达标。"}
    write_json(out_dir / "report.json", report)
    return report
