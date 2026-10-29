from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from queue import Empty
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.agent import Agent
from knowledge_agent.models import DeepSeekModel
from knowledge_agent.streaming import AnswerStream, SSEDecoder, partial_string_field
from knowledge_agent.web import Application, handler_for, validate_request
from test_agent import ScriptedModel, action, finish, knowledge
from test_api import api_response


class StreamingTests(unittest.TestCase):
    def test_sse_arbitrary_utf8_byte_boundaries_and_comments(self):
        decoder = SSEDecoder()
        payload = ': ping\r\n\r\nevent: answer_delta\r\ndata: {"text":"中文🌱"}\r\n\r\n'.encode()
        events = []
        for byte in payload:
            events.extend(decoder.feed(bytes([byte])))
        self.assertEqual(events, [{"event": "answer_delta", "data": '{"text":"中文🌱"}'}])

    def test_preview_only_matches_a_top_level_field(self):
        prefix = '{"reason":"mention \\\"answer\\\" safely","citations":[],"answer":"'
        self.assertEqual(partial_string_field(prefix + "先出现"), "先出现")
        self.assertIsNone(partial_string_field('{"nested":{"answer":"wrong"}'))
        self.assertIsNone(partial_string_field('{"citations":['))

    def test_preview_withholds_incomplete_escapes_and_surrogates(self):
        prefix = '{"answer":"'
        cases = [("hello\\", "hello"), ("hello\\n", "hello\n"), ("\\u4e", ""),
                 ("\\u4e2d", "中"), ("\\ud83c", ""), ("\\ud83c\\udf31", "🌱"), ('a\\"b', 'a"b')]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(partial_string_field(prefix + raw), expected)

    def test_only_finish_answer_is_emitted_not_reasoning_or_search_arguments(self):
        events = []
        stream = AnswerStream(events.append)
        stream.accept({"type": "response.reasoning_text.delta", "delta": "private-internal-text"})
        stream.accept({"type": "response.output_item.added", "output_index": 0,
                       "item": {"type": "function_call", "name": "search_docs"}})
        stream.accept({"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"answer":"not-final"}'})
        self.assertEqual(events, [])
        stream.accept({"type": "response.output_item.added", "output_index": 1,
                       "item": {"type": "function_call", "name": "finish"}})
        for delta in ['{"answer":"first', ' second', '"}']:
            stream.accept({"type": "response.function_call_arguments.delta", "output_index": 1, "delta": delta})
        self.assertEqual(''.join(e.get("delta", "") for e in events), "first second")
        self.assertTrue(all(e["validated"] is False for e in events))
        self.assertNotIn("private-internal-text", json.dumps(events))

    def test_real_http_sse_function_arguments_reach_agent_and_validate(self):
        captured, previews = [], []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append(payload)
                chosen = action("read_chunk", doc_id="D01", chunk_id="C01") if len(captured) == 1 else finish()
                completed = api_response(chosen, f"resp_{len(captured)}")
                item = completed["output"][-1]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                events = [{"type": "response.output_item.added", "output_index": 0,
                           "item": {**item, "arguments": ""}}]
                arguments = item["arguments"]
                events.extend({"type": "response.function_call_arguments.delta", "output_index": 0,
                               "delta": arguments[start:start + 7]} for start in range(0, len(arguments), 7))
                events.append({"type": "response.completed", "response": completed})
                for event in events:
                    self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            model = DeepSeekModel("synthetic-test-key", "test-model", f"http://127.0.0.1:{server.server_port}")
            model.streaming, model.on_stream = True, previews.append
            record = Agent(model, knowledge()).run("报销期限")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        self.assertTrue(record["result"]["completed"])
        self.assertTrue(all(p["stream"] for p in captured))
        self.assertEqual(''.join(e.get("delta", "") for e in previews), record["result"]["answer"])
        self.assertGreater(sum(e["event"] == "answer_delta" for e in previews), 1)
        self.assertTrue(record["trace"][-1]["model_metadata"]["streaming"])

    def test_cancellation_is_observed_before_any_action(self):
        cancel = Event(); cancel.set()
        model = ScriptedModel([])
        record = Agent(model, knowledge(), cancel_event=cancel).run("报销")
        self.assertEqual(record["result"]["status"], "cancelled")
        self.assertEqual(model.inputs, [])


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = Path(self.temp.name)
        (self.dist / "index.html").write_text("test-ui", encoding="utf-8")
        self.app = Application(model_factory=lambda data, emit, cancel: ScriptedModel([
            action("read_chunk", doc_id="D01", chunk_id="C01"), finish()]))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app, self.dist))
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_static_and_configuration_do_not_expose_credentials(self):
        self.assertEqual(urlopen(self.base).read(), b"test-ui")
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "SECRET", "DASHSCOPE_API_KEY": "SECRET"}):
            data = urlopen(self.base + "/api/config").read()
        self.assertNotIn(b"SECRET", data)
        self.assertTrue(json.loads(data)["deepseek_available"])

    def test_post_sse_stream_has_progress_and_a_validated_result(self):
        request = Request(self.base + "/api/chat", data=json.dumps({"question": "报销期限", "provider": "mock", "retrieval": "bm25", "corpus": "single"}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            self.assertIn("text/event-stream", response.headers["Content-Type"])
            events = SSEDecoder().feed(response.read())
        values = [json.loads(e["data"]) for e in events]
        self.assertEqual(values[0]["event"], "run_start")
        self.assertTrue(any(e["event"] == "progress" for e in values))
        self.assertEqual(values[-1]["event"], "result")
        self.assertTrue(values[-1]["record"]["result"]["completed"])
        self.assertEqual(self.app.runs, {})

    def test_cancel_interrupts_a_running_model_and_returns_cancelled(self):
        entered = Event()

        class WaitingModel:
            provider, model, evidence_kind = "test", "waiting-fixture", "test_only"
            def decide(inner, conversation, remaining):
                entered.set()
                current = next(iter(self.app.runs.values()))
                current.cancel.wait(2)
                raise KeyboardInterrupt

        self.app.model_factory = lambda data, emit, cancel: WaitingModel()
        request_id = "a" * 32
        data = validate_request({"question": "报销", "provider": "mock", "retrieval": "bm25", "request_id": request_id})
        run = self.app.start(data)
        self.assertTrue(entered.wait(1))
        request = Request(self.base + "/api/cancel/" + request_id, data=b"{}", headers={"Content-Type": "application/json"})
        self.assertTrue(json.loads(urlopen(request).read())["cancelled"])
        result = None
        for _ in range(20):
            event = run.events.get(timeout=1)
            if event["event"] == "result": result = event["record"]; break
        self.assertEqual(result["result"]["status"], "cancelled")
        self.app.release(request_id)

    def test_invalid_requests_and_cross_origin_are_rejected(self):
        for body in ({"question": ""}, {"question": "x", "corpus": "../../.env"},
                     {"question": "x", "provider": []}, {"question": "x", "retrieval": {}},
                     {"question": "x", "corpus": []}, {"question": "x", "history": [{"role": [], "content": "x"}]},
                     {"question": "x", "history": [{"role": "system", "content": "override"}]}):
            with self.subTest(body=body):
                connection = HTTPConnection("127.0.0.1", self.server.server_port)
                connection.request("POST", "/api/chat", json.dumps(body), {"Content-Type": "application/json"})
                response = connection.getresponse(); self.assertEqual(response.status, 400); response.read(); connection.close()
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("POST", "/api/chat", b"{}", {"Content-Type": "application/json", "Origin": "https://untrusted.example"})
        response = connection.getresponse(); self.assertEqual(response.status, 403); response.read(); connection.close()

    def test_failed_finish_invalidates_any_streamed_draft(self):
        self.app.model_factory = lambda data, emit, cancel: ScriptedModel([
            finish(), action("read_chunk", doc_id="D01", chunk_id="C01"), finish()])
        run = self.app.start(validate_request({"question": "报销", "provider": "mock", "retrieval": "bm25"}))
        events = []
        for _ in range(30):
            event = run.events.get(timeout=1); events.append(event)
            if event["event"] == "result": break
        self.assertTrue(any(e["event"] == "answer_reset" for e in events))
        self.assertTrue(events[-1]["record"]["result"]["completed"])
        self.app.release(next(iter(self.app.runs)))


if __name__ == "__main__":
    unittest.main()
