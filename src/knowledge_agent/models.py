"""Real Responses API adapter and a deliberately limited offline test double."""
from __future__ import annotations

import json
import os
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .prompts import SYSTEM_PROMPT
from .schema import Action, ModelError, TOOLS


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward Authorization to a redirect target.
        return None


class ResponsesModel:
    provider = "openai"
    evidence_kind = "real_model"
    allow_multiple = False

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://api.openai.com/v1", timeout: float = 45,
                 transport=None):
        if not api_key or not model:
            raise ValueError("真实模式须设置 OPENAI_API_KEY 和 OPENAI_MODEL；离线请用 --provider mock")
        parsed = urlsplit(base_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme != "https" and not (local and parsed.scheme == "http")) or not parsed.hostname:
            raise ValueError("OPENAI_BASE_URL 必须为 HTTPS；仅本机调试允许 HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OPENAI_BASE_URL 不允许内嵌凭证、查询参数或 fragment")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.api_key, self.model = api_key, model
        self.url, self.timeout = base_url.rstrip("/") + "/responses", timeout
        self.transport = transport or self._post

    @classmethod
    def from_env(cls, timeout: float = 45) -> "ResponsesModel":
        return cls(os.getenv("OPENAI_API_KEY", ""), os.getenv("OPENAI_MODEL", ""),
                   os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"), timeout)

    def _post(self, payload: dict) -> dict:
        request = Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self.api_key,
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Provider error bodies may echo the key, request or sensitive source text.
            raise ModelError(f"模型 HTTP {exc.code}；请检查模型权限、额度及 API 配置") from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise ModelError("模型网络错误或超时；未取得可用模型动作") from None
        except (ValueError, UnicodeError):
            raise ModelError("模型端点返回了无效 JSON") from None

    def build_payload(self, conversation: list[dict], remaining: int) -> dict:
        return {"model": self.model, "instructions": SYSTEM_PROMPT +
                   f"\n本次还可执行 {remaining} 步（含finish），达到上限将返回未完成。",
                   "input": conversation, "tools": TOOLS, "tool_choice": "required",
                   "parallel_tool_calls": False, "store": False,
                   "include": ["reasoning.encrypted_content"], "max_output_tokens": 3000}
    def decide(self, conversation: list[dict], remaining: int) -> Action | list[Action]:
        response = self.transport(self.build_payload(conversation, remaining))
        try:
            if not isinstance(response, dict) or response.get("status") != "completed":
                raise ValueError
            items = response["output"]
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                raise ValueError
            calls = [item for item in items if item.get("type") == "function_call"]
            if not calls or (len(calls) != 1 and not self.allow_multiple):
                raise ValueError
            usage = response.get("usage") or {}
            safe_usage = {key: value for key, value in usage.items()
                          if key in {"input_tokens", "output_tokens", "total_tokens"}
                          and type(value) is int}
            actions, call_ids = [], set()
            for index, call in enumerate(calls):
                arguments = json.loads(call["arguments"])
                if not isinstance(arguments, dict) or not isinstance(call["name"], str):
                    raise ValueError
                if (not isinstance(call["call_id"], str) or not call["call_id"]
                        or call["call_id"] in call_ids):
                    raise ValueError
                call_ids.add(call["call_id"])
                actions.append(Action(call["name"], arguments, call["call_id"], items if index == 0 else [],
                                      {"response_id": response.get("id"),
                                       "usage": safe_usage if index == 0 else {}}))
            return actions[0] if len(actions) == 1 else actions
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ModelError("模型协议错误：预期 completed 响应、有效工具调用和唯一 call_id") from None


class DeepSeekModel(ResponsesModel):
    """Official DeepSeek Responses endpoint; all decisions still come from the model."""
    provider = "deepseek"
    allow_multiple = True

    @classmethod
    def from_env(cls, timeout: float = 45) -> "DeepSeekModel":
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            raise ValueError("真实模式须在本机设置 DEEPSEEK_API_KEY；离线请用 --provider mock")
        return cls(key, os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                   os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), timeout)

    def build_payload(self, conversation: list[dict], remaining: int) -> dict:
        payload = super().build_payload(conversation, remaining)
        for unsupported in ("include", "store", "parallel_tool_calls"):
            payload.pop(unsupported)
        payload["tools"] = [{k: v for k, v in tool.items() if k != "strict"} for tool in TOOLS]
        # Official Responses schema: none disables thinking. Logs never store CoT.
        payload["reasoning"] = {"effort": "none"}
        payload["temperature"] = 0
        return payload


class MockModel:
    """Rules for bundled synthetic cases only. NOT an LLM or ReAct evidence.

    It only sees the same conversation/tool observations as a real model, but its
    next action is determined by code. Trace files explicitly label this boundary.
    """
    provider = "mock"
    model = "offline-rules-v1"
    evidence_kind = "mock_flow_only"

    @staticmethod
    def action(name: str, reason: str, **arguments) -> Action:
        return Action(name, {**arguments, "reason": reason})

    def finish(self, status: str, answer: str, reason: str,
               citations=None, gaps=None, conflicts=None) -> Action:
        return self.action("finish", reason, status=status, answer=answer,
                           citations=citations or [], gaps=gaps or [], conflicts=conflicts or [])

    def decide(self, conversation: list[dict], remaining: int) -> Action:
        request = json.loads(conversation[0]["content"])
        question = request["question"]
        context = " ".join(item["content"] for item in request["history"])
        combined = context + " " + question
        observations = [json.loads(item["output"]) for item in conversation
                        if item.get("type") == "function_call_output"]
        searches = [o for o in observations if "query" in o]
        reads = [o["chunk"] for o in observations if o.get("ok") and "chunk" in o]
        errors = [o for o in observations if not o.get("ok")]
        if errors and not observations[-1].get("ok"):
            error = observations[-1].get("error", {})
            if error.get("code") == "UNAVAILABLE" and len(errors) == 1:
                prior = [item for item in conversation if item.get("type") == "function_call"][-1]
                arguments = json.loads(prior["arguments"])
                arguments["reason"] = "离线规则：观察到一次可恢复工具故障，选择再试一次。"
                return Action(prior["name"], arguments)
            return self.finish("tool_error", "必要工具不可用，当前无法可靠回答。",
                               "离线规则：工具仍失败，停止并保留已有记录。", gaps=["需要恢复工具服务后重试。"])

        if not observations:
            if not context and re.fullmatch(r"[那这它].{0,8}[？?]?", question.strip()):
                return self.finish("needs_clarification", "请说明你询问的是哪项制度或产品。",
                                   "离线规则：代词没有可用对话上下文。", gaps=["缺少问题对象。"])
            query = question
            if context:
                query = "报销材料" if any(w in question for w in ("材料", "凭证", "发票")) else combined
            return self.action("search_docs", "离线规则：先按当前问题及必要历史检索。", query=query, top_k=8)

        if not searches:
            return self.finish("insufficient_evidence", "没有可用检索结果。", "离线规则：停止。",
                               gaps=["缺少相关文档。"])
        latest = searches[-1]
        if not latest["hits"]:
            if len(searches) == 1:
                # Do not add generic domain terms: that can retrieve unrelated policies.
                rewritten = "报销期限" if any(w in combined for w in ("报账", "垫付", "垫的钱", "票据")) else question.strip("？?。 ")
                return self.action("search_docs", "离线规则：首轮无结果，尝试同义词或扩展查询。",
                                   query=rewritten, top_k=8)
            return self.finish("insufficient_evidence", "现有知识库没有支持该问题的资料，无法可靠回答。",
                               "离线规则：补充检索仍无结果。", gaps=["请补充相关制度或产品文档。"])

        # Fixture-only topic selection; a real model performs semantic relevance judgment.
        query = latest["query"]
        if any(w in query for w in ("材料", "凭证", "发票")):
            relevant = [h for h in latest["hits"] if "材料" in h["title"]]
        elif any(w in combined + query for w in ("报销", "报账", "垫的钱", "票据")):
            relevant = [h for h in latest["hits"] if "期限" in h["title"]]
        else:
            relevant = latest["hits"][:1]
        read_keys = {(r["doc_id"], r["chunk_id"]) for r in reads}
        for hit in relevant:
            if (hit["doc_id"], hit["chunk_id"]) not in read_keys:
                return self.action("read_chunk", "离线规则：候选仅是线索，需要读取原文。",
                                   doc_id=hit["doc_id"], chunk_id=hit["chunk_id"])
        if not reads:
            return self.finish("insufficient_evidence", "检索候选与问题不匹配。", "离线规则：无可引用原文。",
                               gaps=["缺少相关原文。"])
        deadlines = {m.group(1) for r in reads
                     if (m := re.search(r"报销期限为\s*(\d+)\s*天", r["text"]))}
        citations = [{key: r[key] for key in ("doc_id", "chunk_id", "version_date")} |
                     {"quote": r["text"]} for r in reads]
        if len(deadlines) > 1:
            if len(searches) < 2:
                return self.action("search_docs", "离线规则：观察到期限冲突，补查适用范围及优先级。",
                                   query="报销期限 适用范围 优先级 替代关系", top_k=8)
            answer = "资料存在冲突，无法确定适用期限：" + "；".join(
                f"[{r['doc_id']}/{r['chunk_id']}] {r['text']}" for r in reads)
            answer += " 请确认适用的制度或部门。版本日期本身不能证明优先级。"
            return self.finish("needs_clarification", answer, "离线规则：补查没有找到有效裁决依据。",
                               citations=citations, gaps=["请确认适用制度、部门或提供明确的优先级说明。"],
                               conflicts=[{"description": "报销期限出现不同天数，适用范围与优先级未明确。",
                                           "doc_ids": [r["doc_id"] for r in reads]}])
        answer = "\n".join(f"依据 [{r['doc_id']}/{r['chunk_id']}]：{r['text']}" for r in reads)
        return self.finish("answered", answer, "离线规则：已读取原文，按原文作答。", citations=citations)
