"""Bounded tool loop. This runtime never chooses the next business action."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from uuid import uuid4

from .knowledge import KnowledgeBase, ToolError
from .schema import ModelError, TOOL_MAP, validate


def summary(observation: dict) -> str:
    if not observation.get("ok"):
        return "失败：" + observation["error"]["code"]
    if "hits" in observation:
        return f"命中 {observation['total_matches']} 个片段；本次返回 {len(observation['hits'])} 个"
    if "chunk" in observation:
        chunk = observation["chunk"]
        return f"读取 {chunk['doc_id']}/{chunk['chunk_id']}，版本 {chunk['version_date']}"
    return "最终结果通过结构与引用校验"


class Agent:
    def __init__(self, model, knowledge: KnowledgeBase, max_steps: int = 12):
        if type(max_steps) is not int or not 1 <= max_steps <= 50:
            raise ValueError("max_steps must be an integer between 1 and 50")
        self.model, self.knowledge, self.max_steps = model, knowledge, max_steps

    @staticmethod
    def validate_final(arguments: dict, evidence: dict) -> dict:
        result = {key: value for key, value in arguments.items() if key != "reason"}
        if not result["answer"].strip():
            raise ValueError("answer must not be empty")
        status, citations = result["status"], result["citations"]
        if status == "answered" and not citations:
            raise ValueError("answered requires citations from successful reads")
        if status == "answered" and (result["gaps"] or result["conflicts"]):
            raise ValueError("answered cannot contain unresolved gaps or conflicts")
        if status != "answered" and not any(gap.strip() for gap in result["gaps"]):
            raise ValueError("incomplete result requires an explicit gap")
        cited = set()
        cited_chunks = set()
        for citation in citations:
            key = (citation["doc_id"], citation["chunk_id"])
            source = evidence.get(key)
            if not source:
                raise ValueError("citation must reference a chunk read successfully in this run")
            quote = citation["quote"]
            if len(quote.strip()) < 6 or quote not in source["text"]:
                raise ValueError("citation quote must be an exact source substring of at least 6 characters")
            if citation["version_date"] != source["version_date"]:
                raise ValueError("citation version_date does not match the source")
            cited.add(citation["doc_id"])
            cited_chunks.add(key)
        inline_references = set(re.findall(r"\[([^\[\]/\s]+)/([^\[\]/\s]+)\]", result["answer"]))
        if not inline_references <= cited_chunks:
            raise ValueError("answer中的每个[doc_id/chunk_id]引用都必须在citations中提供对应的版本与原文片段")
        if result["conflicts"] and status != "needs_clarification":
            raise ValueError("unresolved conflicts require needs_clarification")
        for conflict in result["conflicts"]:
            if len(set(conflict["doc_ids"])) < 2 or not set(conflict["doc_ids"]) <= cited:
                raise ValueError("conflict must cite at least two distinct documents")
            if not conflict["description"].strip():
                raise ValueError("conflict requires a description")
        return {**result, "completed": status == "answered"}

    def run(self, question: str, history: list[dict] | None = None) -> dict:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must not be empty")
        history = [] if history is None else history
        if not isinstance(history, list):
            raise ValueError("history must be a JSON array")
        for item in history:
            if (not isinstance(item, dict) or set(item) != {"role", "content"}
                    or item["role"] not in ("user", "assistant")
                    or not isinstance(item["content"], str)):
                raise ValueError("history items require role=user|assistant and string content")
        started, wall = datetime.now(timezone.utc).isoformat(), time.monotonic()
        request = {"question": question.strip(), "history": history}
        conversation = [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
        evidence, trace = {}, []
        result = None
        decision_round = 0
        while len(trace) < self.max_steps and result is None:
            decision_round += 1
            try:
                chosen = self.model.decide(conversation, self.max_steps - len(trace))
            except ModelError as exc:
                if trace:
                    trace[-1]["next_action"] = "stop:model_error"
                trace.append({"step": len(trace) + 1, "decision_round": decision_round,
                              "tool": None, "arguments": {},
                              "decision_summary": "未获得可执行模型动作。", "observation":
                              {"ok": False, "error": {"code": "MODEL_ERROR", "message": str(exc)}},
                              "observation_summary": "模型请求或协议失败", "next_action": "stop:model_error"})
                result = self._incomplete("model_error", "模型不可用，任务未完成。", str(exc))
                break
            actions = chosen if isinstance(chosen, list) else [chosen]
            for index, item in enumerate(actions):
                if not item.call_id:
                    item.call_id = f"local_{len(trace) + index + 1}"
            conversation.extend(actions[0].raw_items or [
                {"type": "function_call", "call_id": a.call_id, "name": a.name,
                 "arguments": json.dumps(a.arguments, ensure_ascii=False)} for a in actions])
            for action in actions:
                if len(trace) >= self.max_steps:
                    break
                if trace:
                    trace[-1]["next_action"] = action.name
                    trace[-1]["next_action_source"] = (
                        "same_model_response" if trace[-1].get("decision_round") == decision_round
                        else "new_model_response_after_observation")
                entry = {"step": len(trace) + 1, "decision_round": decision_round,
                         "tool": action.name,
                         "arguments": {k: v for k, v in action.arguments.items() if k != "reason"},
                         "decision_summary": action.arguments.get("reason", ""),
                         "model_metadata": action.metadata, "next_action": None}
                try:
                    if action.name not in TOOL_MAP:
                        raise ValueError("unknown tool")
                    validate(action.arguments, TOOL_MAP[action.name]["parameters"])
                    if not action.arguments["reason"].strip():
                        raise ValueError("reason requires a brief action summary")
                    arguments = {k: v for k, v in action.arguments.items() if k != "reason"}
                    if action.name == "finish":
                        if len(actions) != 1:
                            raise ValueError("finish must be called alone after observing tool results")
                        if arguments["conflicts"]:
                            conflicting_ids = {doc_id for conflict in arguments["conflicts"]
                                               for doc_id in conflict["doc_ids"]}
                            read_steps = [t["step"] for t in trace
                                          if t["observation"].get("chunk", {}).get("doc_id") in conflicting_ids
                                          and t["observation"].get("ok")]
                            if not read_steps or not any(
                                t["tool"] == "search_docs" and t["observation"].get("ok")
                                and t["step"] > max(read_steps) for t in trace
                            ):
                                raise ValueError("提交冲突前须在读取双方后补充检索适用范围、优先级或替代关系以查证；不能直接结束")
                        result = self.validate_final(arguments, evidence)
                        observation = {"ok": True, "status": result["status"]}
                    elif action.name == "search_docs":
                        observation = self.knowledge.search_docs(**arguments)
                    else:
                        observation = self.knowledge.read_chunk(**arguments)
                        chunk = observation["chunk"]
                        evidence[(chunk["doc_id"], chunk["chunk_id"])] = chunk
                except ToolError as exc:
                    observation = exc.observation()
                except ValueError as exc:
                    observation = {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(exc),
                                                           "retryable": False}}
                entry["observation"] = observation
                entry["observation_summary"] = summary(observation)
                trace.append(entry)
                conversation.append({"type": "function_call_output", "call_id": action.call_id,
                                     "output": json.dumps(observation, ensure_ascii=False)})
                if result is not None:
                    entry["next_action"] = "stop:" + result["status"]
                    break
        if result is None:
            result = self._incomplete("step_limit", "达到执行步数上限，任务未完成。",
                                      "需要更多工具预算或缩小问题范围，不能把中间证据作为完整答案。")
            trace[-1]["next_action"] = "stop:step_limit"
        return {"run_id": uuid4().hex, "started_at_utc": started,
                "elapsed_seconds": round(time.monotonic() - wall, 4),
                "provider": self.model.provider, "model": self.model.model,
                "evidence_kind": self.model.evidence_kind, "input": request,
                "corpus_sha256": self.knowledge.sha256, "limits": {"max_steps": self.max_steps},
                "steps_used": len(trace), "result": result, "trace": trace}

    @staticmethod
    def _incomplete(status: str, answer: str, gap: str) -> dict:
        return {"status": status, "completed": False, "answer": answer,
                "citations": [], "gaps": [gap], "conflicts": []}
