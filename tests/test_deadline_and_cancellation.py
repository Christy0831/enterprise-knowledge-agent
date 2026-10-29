"""Regression for continuous HTTP keep-alives and user cancellation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.agent import Agent
from knowledge_agent.cli import main
from knowledge_agent.embeddings import DashScopeEmbedder, EmbeddingError
from knowledge_agent.evaluation import evaluate
from knowledge_agent.http_json import read_json, RequestDeadlineExceeded
from knowledge_agent.models import DeepSeekModel, MockModel, ResponsesModel
from knowledge_agent.schema import ModelError
from test_agent import ScriptedModel, action, finish, knowledge


class DeadlineTests(unittest.TestCase):
    def test_keep_alive_bytes_do_not_extend_total_model_or_embedding_deadline(self):
        keepalive_count = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args): pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    for _ in range(50):
                        # Simulate DeepSeek's non-streaming blank-line keep-alives.
                        self.wfile.write(b"1\r\n\n\r\n")
                        self.wfile.flush()
                        keepalive_count.append(1)
                        time.sleep(0.02)
                    self.wfile.write(b"0\r\n\r\n")
                except OSError:
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            started = time.monotonic()
            model = DeepSeekModel("synthetic-key", "test-model", f"http://127.0.0.1:{server.server_port}", timeout=0.2)
            record = Agent(model, knowledge()).run("报销")
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(record["result"]["status"], "model_error")
            self.assertIn("总时限", record["result"]["gaps"][0])
            client = DashScopeEmbedder("synthetic-key", url=f"http://127.0.0.1:{server.server_port}/embeddings", timeout=0.2)
            with self.assertRaises(EmbeddingError) as caught:
                client.embed(["query"], "query")
            self.assertEqual(caught.exception.code, "REQUEST_DEADLINE")
            self.assertGreater(len(keepalive_count), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_slow_connect_is_bounded_and_late_result_is_not_accepted(self):
        released, closed = Event(), Event()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): closed.set()
            def read1(self, size): return b""

        class Opener:
            def open(self, request, timeout):
                released.wait(2)
                return Response()

        started = time.monotonic()
        try:
            with self.assertRaises(RequestDeadlineExceeded):
                read_json(None, Opener(), 0.05)
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            released.set()
        self.assertTrue(closed.wait(1))

    def test_model_rejects_nonfinite_timeout(self):
        for timeout in (float("nan"), float("inf"), 0):
            with self.assertRaises(ValueError):
                ResponsesModel("test-key", "test-model", timeout=timeout)


class CancellationTests(unittest.TestCase):
    def test_interrupt_during_model_wait_preserves_completed_steps(self):
        model = ScriptedModel([action("read_chunk", doc_id="D01", chunk_id="C01"), KeyboardInterrupt()])
        record = Agent(model, knowledge()).run("报销期限")
        self.assertEqual(record["result"]["status"], "cancelled")
        self.assertFalse(record["result"]["completed"])
        self.assertTrue(record["trace"][0]["observation"]["ok"])
        self.assertEqual(record["trace"][-1]["observation"]["error"]["code"], "CANCELLED")

    def test_interrupt_during_tool_stops_without_another_model_call(self):
        kb = knowledge()
        model = ScriptedModel([action("search_docs", query="报销", top_k=8)])
        with patch.object(kb, "search_docs", side_effect=KeyboardInterrupt()):
            record = Agent(model, kb).run("报销")
        self.assertEqual(record["result"]["status"], "cancelled")
        self.assertEqual(len(model.inputs), 1)

    def test_cli_interrupt_returns_130_and_saves_a_record(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cancelled.json"
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("knowledge_agent.cli.MockModel", return_value=ScriptedModel([KeyboardInterrupt()])), \
                    patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                code = main(["ask", "报销", "--output", str(output)])
            self.assertEqual(code, 130)
            self.assertEqual(json.loads(stdout.getvalue())["result"]["status"], "cancelled")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["result"]["status"], "cancelled")
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_early_interrupt_has_no_traceback(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("knowledge_agent.cli.load_env", side_effect=KeyboardInterrupt()), \
                patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = main(["ask", "报销", "--provider", "deepseek"])
        self.assertEqual(code, 130)
        self.assertIn("已取消", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_evaluation_does_not_continue_after_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            report = evaluate(ScriptedModel([KeyboardInterrupt()]), Path(directory))
        self.assertTrue(report["cancelled"])
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["requested_total"], 11)

    def test_progress_goes_to_stderr_and_quiet_keeps_stdout_json(self):
        for quiet in (False, True):
            model = ScriptedModel([action("read_chunk", doc_id="D01", chunk_id="C01"), finish()])
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("knowledge_agent.cli.load_env"), patch("knowledge_agent.cli.DeepSeekModel.from_env", return_value=model), \
                    patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                code = main(["ask", "报销", "--provider", "deepseek"] + (["--quiet"] if quiet else []))
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout.getvalue())["result"]["completed"])
            self.assertEqual(bool(stderr.getvalue()), not quiet)
            if not quiet:
                self.assertIn("等待", stderr.getvalue())
                self.assertIn("read_chunk", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
