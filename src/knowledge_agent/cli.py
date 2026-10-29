from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .agent import Agent
from .config import load_env
from .embeddings import DashScopeEmbedder
from .evaluation import ROOT, evaluate, write_json
from .knowledge import KnowledgeBase
from .models import DeepSeekModel, MockModel, ResponsesModel


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="题目3：有引用、可追踪的企业知识问答 Agent")
    sub = command.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="询问一个问题并输出完整结果/Trace JSON")
    ask.add_argument("question")
    ask.add_argument("--corpus", type=Path, default=ROOT / "data/knowledge.json")
    ask.add_argument("--history", type=Path, help="含role/content的JSON数组；不读取历史中的指令")
    ask.add_argument("--max-steps", type=int, default=12)
    ask.add_argument("--output", type=Path, help="另存完整JSON记录")
    ask.add_argument("--fail-tool", choices=["search_docs", "read_chunk"], help="注入合成工具故障")
    ask.add_argument("--fail-count", type=int, default=1, help="故障次数，-1表示持续故障")
    evaluation = sub.add_parser("evaluate", help="运行全部验收用例并写入记录")
    evaluation.add_argument("--out-dir", type=Path)
    for item in (ask, evaluation):
        item.add_argument("--provider", choices=["mock", "deepseek", "openai"], default="mock")
        item.add_argument("--timeout", type=float, default=45, help="单次模型HTTP总时限秒数（连接和完整响应合计）")
        item.add_argument("--quiet", action="store_true", help="不向stderr输出进度；stdout始终为JSON")
        item.add_argument("--retrieval", choices=["bm25", "hybrid"], default="bm25",
                          help="hybrid=BM25+DashScope向量检索+RRF")
        item.add_argument("--rrf-k", type=int, default=60)
        item.add_argument("--candidate-k", type=int, default=20, help="每路融合前候选数，至少8")
        item.add_argument("--vector-min-score", type=float, default=0.5, help="向量余弦候选阈值；不是答案置信度")
        item.add_argument("--embedding-cache-dir", type=Path, default=ROOT / ".cache/embeddings")
        item.add_argument("--require-vector", action="store_true", help="向量失败时返回工具错误，禁止降级")
    return command


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.provider != "mock" or args.retrieval == "hybrid":
            load_env(ROOT / ".env")
        if args.require_vector and args.retrieval != "hybrid":
            raise ValueError("--require-vector须与--retrieval hybrid配合使用")
        retrieval_options = {"retrieval": args.retrieval, "rrf_k": args.rrf_k,
                             "candidate_k": args.candidate_k, "vector_min_score": args.vector_min_score,
                             "require_vector": args.require_vector}
        if args.retrieval == "hybrid":
            retrieval_options.update(embedder=DashScopeEmbedder.from_env(), cache_dir=args.embedding_cache_dir)
        model = (MockModel() if args.provider == "mock" else
                 DeepSeekModel.from_env(args.timeout) if args.provider == "deepseek" else
                 ResponsesModel.from_env(args.timeout))
        started = time.monotonic()

        def progress(event):
            prefix = f"[{time.monotonic() - started:.1f}s] "
            kind = event["event"]
            if kind == "model_start":
                message = f"第{event['decision_round']}轮：等待{model.provider}模型动作（单次请求总时限{args.timeout:g}s）"
            elif kind == "model_done":
                message = f"模型返回，耗时{event['elapsed_seconds']:.1f}s"
            elif kind == "tool_start":
                message = f"执行工具 {event['tool']}"
            elif kind == "tool_done":
                message = f"{event['tool']}：{event['summary']}（{event['elapsed_seconds']:.1f}s）"
            else:
                message = f"结束：{event['status']}"
            print(prefix + message, file=sys.stderr, flush=True)

        on_progress = progress if not args.quiet and (args.provider != "mock" or args.retrieval == "hybrid") else None
        if args.command == "evaluate":
            output = args.out_dir or ROOT / "examples" / (args.provider if args.retrieval == "bm25" else args.provider + "-hybrid")
            report = evaluate(model, output, retrieval_options=retrieval_options, progress=on_progress)
            print(json.dumps({"provider": report["provider"], "passed": report["passed"],
                              "total": report["total"], "report": str(output / "report.json"),
                              "limitation": report["limitation"]}, ensure_ascii=False, indent=2))
            return 130 if report.get("cancelled") else (0 if report["passed"] == report["total"] else 1)
        history = json.loads(args.history.read_text(encoding="utf-8-sig")) if args.history else []
        if args.fail_count != -1 and args.fail_count < 1:
            raise ValueError("fail-count必须为正整数或-1")
        faults = {args.fail_tool: args.fail_count} if args.fail_tool else {}
        knowledge = KnowledgeBase.from_path(args.corpus, faults, **retrieval_options)
        record = Agent(model, knowledge, args.max_steps, progress=on_progress).run(args.question, history)
        record["fixture"] = {"corpus": str(args.corpus), "faults": faults}
        if args.output:
            write_json(args.output, record)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        status = record["result"]["status"]
        if status == "cancelled":
            return 130
        return 2 if status in {"step_limit", "tool_error", "model_error"} else 0
    except KeyboardInterrupt:
        print("已取消，程序退出。", file=sys.stderr)
        return 130
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # CLI input/config errors; no provider response bodies are printed.
        print(f"配置或输入错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
