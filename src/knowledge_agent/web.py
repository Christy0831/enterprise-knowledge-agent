"""Local React host and POST SSE API. Credentials stay in the Python process."""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import shutil
import subprocess
from threading import Event, Lock, Semaphore, Thread
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .agent import Agent
from .embeddings import DashScopeEmbedder
from .evaluation import ROOT
from .knowledge import KnowledgeBase
from .models import DeepSeekModel, MockModel

CORPORA = {"conflict": ROOT / "data/knowledge.json", "single": ROOT / "data/fixtures/single_policy.json"}


def configuration() -> dict:
    return {"deepseek_available": bool(os.getenv("DEEPSEEK_API_KEY")),
            "hybrid_available": bool(os.getenv("DASHSCOPE_API_KEY")),
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            "corpora": [{"id": "conflict", "label": "企业资料 · 含冲突制度"},
                        {"id": "single", "label": "报销制度 · 单一版本"}]}


def validate_request(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - {"question", "history", "provider", "retrieval", "corpus", "request_id"}:
        raise ValueError("请求字段无效")
    question = data.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("问题须为1至4000个字符")
    history = data.get("history", [])
    if not isinstance(history, list) or len(history) > 20:
        raise ValueError("历史最多20条")
    for item in history:
        if (not isinstance(item, dict) or set(item) != {"role", "content"}
                or not isinstance(item["role"], str) or item["role"] not in {"user", "assistant"} or not isinstance(item["content"], str)
                or len(item["content"]) > 12000):
            raise ValueError("历史格式无效")
    provider, retrieval, corpus = data.get("provider", "deepseek"), data.get("retrieval", "hybrid"), data.get("corpus", "conflict")
    if (not all(isinstance(value, str) for value in (provider, retrieval, corpus))
            or provider not in {"deepseek", "mock"} or retrieval not in {"bm25", "hybrid"} or corpus not in CORPORA):
        raise ValueError("模型、检索模式或知识库选项无效")
    request_id = data.get("request_id", uuid4().hex)
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("request_id格式无效")
    return {"question": question.strip(), "history": history, "provider": provider,
            "retrieval": retrieval, "corpus": corpus, "request_id": request_id}


@dataclass
class Run:
    cancel: Event = field(default_factory=Event)
    events: Queue = field(default_factory=lambda: Queue(maxsize=4096))
    done: bool = False


class Application:
    def __init__(self, *, timeout=45, max_steps=12, model_factory=None):
        self.timeout, self.max_steps, self.model_factory = timeout, max_steps, model_factory
        self.runs: dict[str, Run] = {}
        self.lock, self.slots = Lock(), Semaphore(4)

    def start(self, data: dict) -> Run:
        run = Run()
        with self.lock:
            if data["request_id"] in self.runs:
                raise ValueError("请求ID已使用")
            if not self.slots.acquire(blocking=False):
                raise ValueError("同时执行任务已达上限，请稍后再试")
            self.runs[data["request_id"]] = run

        def emit(event):
            if not run.cancel.is_set() or event["event"] in {"result", "error", "finished"}:
                try:
                    run.events.put_nowait(event)
                except Full:
                    run.cancel.set()
                    if event["event"] in {"result", "error"}:
                        try:
                            run.events.get_nowait()
                        except Empty:
                            pass
                        run.events.put_nowait(event)

        def worker():
            try:
                def progress(event):
                    if event.get("event") == "tool_done" and event.get("tool") == "finish" and not event.get("ok"):
                        emit({"event": "answer_reset", "message": "校验未通过，正在修正回答。"})
                    emit({"event": "progress", **{k: v for k, v in event.items() if k != "event"}, "phase": event["event"]})

                model = self.model_factory(data, emit, run.cancel) if self.model_factory else (
                    DeepSeekModel.from_env(self.timeout) if data["provider"] == "deepseek" else MockModel())
                if isinstance(model, DeepSeekModel):
                    model.streaming, model.on_stream, model.cancel_event = True, emit, run.cancel
                options = {"retrieval": data["retrieval"]}
                if data["retrieval"] == "hybrid":
                    embedder = DashScopeEmbedder.from_env()
                    embedder.cancel_event = run.cancel
                    options.update(embedder=embedder, cache_dir=ROOT / ".cache/embeddings")
                kb = KnowledgeBase.from_path(CORPORA[data["corpus"]], **options)
                record = Agent(model, kb, self.max_steps, progress=progress, cancel_event=run.cancel).run(data["question"], data["history"])
                emit({"event": "result", "record": record})
            except (ValueError, OSError) as exc:
                # These errors are local configuration/input errors, not provider response bodies.
                emit({"event": "error", "message": str(exc)})
            except BaseException:
                emit({"event": "error", "message": "服务执行失败，请查看本机运行环境后重试。"})
            finally:
                run.done = True
                self.slots.release()

        Thread(target=worker, daemon=True, name="knowledge-web-run").start()
        return run

    def stop(self, request_id):
        with self.lock:
            run = self.runs.get(request_id)
        if run is None:
            return False
        run.cancel.set()
        return True

    def release(self, request_id):
        with self.lock:
            self.runs.pop(request_id, None)


def handler_for(app: Application, dist: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _valid_origin(self):
            host = self.headers.get("Host", "")
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            allowed_origins = {"http://" + name for name in allowed_hosts} | {"http://127.0.0.1:5173", "http://localhost:5173"}
            return origin is None or origin in allowed_origins

        def _json(self, code, data):
            body = json.dumps(data, ensure_ascii=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._valid_origin():
                return self._json(403, {"error": "只允许本机页面访问"})
            path = urlsplit(self.path).path
            if path == "/api/config":
                return self._json(200, configuration())
            if path.startswith("/api/"):
                return self._json(404, {"error": "接口不存在"})
            target = (dist / unquote(path).lstrip("/")).resolve()
            if not target.is_relative_to(dist.resolve()):
                return self._json(404, {"error": "文件不存在"})
            if path == "/":
                target = dist / "index.html"
            if not target.is_file():
                return self._json(404, {"error": "前端尚未构建，请运行 python run.py serve --build"})
            body = target.read_bytes()
            self.send_response(200)
            mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self._valid_origin():
                return self._json(403, {"error": "只允许本机页面访问"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._json(415, {"error": "请使用application/json"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("请求体为空或超过64KB")
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                path = urlsplit(self.path).path
                if path.startswith("/api/cancel/"):
                    return self._json(200, {"cancelled": app.stop(path.removeprefix("/api/cancel/"))})
                if path != "/api/chat":
                    return self._json(404, {"error": "接口不存在"})
                data = validate_request(data)
                run = app.start(data)
            except (ValueError, UnicodeError):
                return self._json(400, {"error": "请求参数无效或服务繁忙，请检查输入后重试"})
            def send(event):
                body = json.dumps(event, ensure_ascii=True)
                self.wfile.write(("event: " + event["event"] + "\ndata: " + body + "\n\n").encode())
                self.wfile.flush()

            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                send({"event": "run_start", "request_id": data["request_id"], "provider": data["provider"]})
                last_ping = time.monotonic()
                while True:
                    try:
                        event = run.events.get(timeout=0.25)
                    except Empty:
                        if run.done:
                            break
                        if time.monotonic() - last_ping >= 5:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                            last_ping = time.monotonic()
                        continue
                    send(event)
                    if event["event"] in {"result", "error"}:
                        break
            except (OSError, BrokenPipeError):
                run.cancel.set()
            finally:
                app.release(data["request_id"])
    return Handler


def build_frontend():
    node = shutil.which("node")
    if node is None and os.name == "nt":
        candidates = sorted((Path(os.getenv("LOCALAPPDATA", "")) / "CodexTools").glob("node-v24*-win-x64/node.exe"))
        node = str(candidates[-1]) if candidates else None
    if node is None:
        raise ValueError("构建React需要Node.js 22.12+或24，请安装后重试")
    if os.name == "nt":
        npm = Path(node).parent / "node_modules/npm/bin/npm-cli.js"
    else:
        npm_command = shutil.which("npm")
        npm = Path(npm_command).resolve() if npm_command else Path("/missing-npm")
    if not npm.is_file():
        raise ValueError("未找到npm，请检查Node.js安装")
    directory = ROOT / "frontend"
    environment = os.environ.copy()
    environment["PATH"] = str(Path(node).parent) + os.pathsep + environment.get("PATH", "")
    for key in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        environment.pop(key, None)
    try:
        subprocess.run([node, str(npm), "ci", "--no-audit", "--no-fund"], cwd=directory, env=environment, check=True)
        subprocess.run([node, str(npm), "run", "build"], cwd=directory, env=environment, check=True)
    except subprocess.CalledProcessError:
        raise ValueError("前端安装或构建失败，请查看上方npm错误后重试") from None


def serve(host="127.0.0.1", port=8765, timeout=45):
    app = Application(timeout=timeout)
    server = ThreadingHTTPServer((host, port), handler_for(app, ROOT / "frontend/dist"))
    print(f"知识问答UI：http://{host}:{server.server_port}  （Ctrl+C停止）", flush=True)
    try:
        server.serve_forever()
    finally:
        with app.lock:
            for run in app.runs.values():
                run.cancel.set()
        server.server_close()
