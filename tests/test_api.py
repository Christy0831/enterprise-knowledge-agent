"""Protocol/HTTP tests use synthetic API responses, not a paid or real model."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.agent import Agent
from knowledge_agent.models import DeepSeekModel, NoRedirect, ResponsesModel
from knowledge_agent.schema import ModelError
from test_agent import action, citation, finish, knowledge


def api_response(item, response_id="resp_test"):
    return {"id": response_id, "status": "completed", "output": [
        {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "opaque-test-only"},
        {"type": "function_call", "call_id": "call_" + response_id, "name": item.name,
         "arguments": json.dumps(item.arguments, ensure_ascii=False)}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}


class ApiTests(unittest.TestCase):
    def test_deepseek_payload_uses_official_responses_compatibility(self):
        captured = []

        def transport(payload):
            captured.append(payload)
            return api_response(action("search_docs", query="报销期限", top_k=8))

        model = DeepSeekModel("test-key", "deepseek-flash", "https://api.deepseek.com", transport=transport)
        model.decide([], 4)
        payload = captured[0]
        self.assertEqual(model.url, "https://api.deepseek.com/responses")
        self.assertEqual(payload["reasoning"], {"effort": "none"})
        self.assertEqual(payload["temperature"], 0)
        self.assertNotIn("include", payload)
        self.assertNotIn("store", payload)
        self.assertNotIn("parallel_tool_calls", payload)
        self.assertTrue(all("strict" not in tool for tool in payload["tools"]))

    def test_deepseek_parallel_reads_are_paired_and_counted_individually(self):
        parallel = api_response(action("read_chunk", doc_id="D01", chunk_id="C01"))
        parallel["output"].append({"type": "function_call", "call_id": "call_second",
                                   "name": "read_chunk", "arguments": json.dumps({
                                       "doc_id": "D03", "chunk_id": "C01", "reason": "读取材料"})})
        responses = iter([parallel, api_response(finish())])
        captured = []

        def transport(payload):
            captured.append(json.loads(json.dumps(payload)))
            return next(responses)

        model = DeepSeekModel("test-key", "deepseek-flash", transport=transport)
        record = Agent(model, knowledge()).run("报销")
        self.assertEqual(record["steps_used"], 3)
        self.assertEqual([t["decision_round"] for t in record["trace"]], [1, 1, 2])
        self.assertEqual(record["trace"][0]["next_action_source"], "same_model_response")
        self.assertEqual(record["trace"][1]["next_action_source"], "new_model_response_after_observation")
        results = [i for i in captured[1]["input"] if i.get("type") == "function_call_output"]
        self.assertEqual({r["call_id"] for r in results}, {"call_resp_test", "call_second"})
        self.assertTrue(record["result"]["completed"])
        limited = Agent(DeepSeekModel("test-key", "deepseek-flash", transport=lambda p: parallel), knowledge(), 1).run("报销")
        self.assertEqual(limited["steps_used"], 1)
        self.assertEqual(limited["result"]["status"], "step_limit")

    def test_finish_cannot_share_a_batch_with_unobserved_tools(self):
        parallel = api_response(action("read_chunk", doc_id="D01", chunk_id="C01"))
        parallel["output"].append({"type": "function_call", "call_id": "call_final",
                                   "name": "finish", "arguments": json.dumps(finish().arguments)})
        responses = iter([parallel, api_response(finish())])
        model = DeepSeekModel("test-key", "deepseek-flash", transport=lambda p: next(responses))
        record = Agent(model, knowledge()).run("报销")
        self.assertEqual(record["trace"][1]["observation"]["error"]["code"], "VALIDATION_ERROR")
        self.assertTrue(record["result"]["completed"])

    def test_http_roundtrip_and_observation_driven_response(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, self.headers.get("Authorization"), request))
                observations = [json.loads(i["output"]) for i in request["input"]
                                if i.get("type") == "function_call_output"]
                if not observations:
                    chosen = action("search_docs", query="报销期限", top_k=8)
                elif "hits" in observations[-1]:
                    hit = observations[-1]["hits"][0]
                    chosen = action("read_chunk", doc_id=hit["doc_id"], chunk_id=hit["chunk_id"])
                else:
                    chunk = observations[-1]["chunk"]
                    chosen = finish(citations=[citation(chunk["text"], chunk["version_date"])])
                body = json.dumps(api_response(chosen, f"resp_{len(captured)}")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            model = ResponsesModel("test-key-only", "test-model", f"http://127.0.0.1:{server.server_port}/v1")
            record = Agent(model, knowledge()).run("报销期限多久")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertTrue(record["result"]["completed"])
        self.assertEqual(len(captured), 3)
        self.assertEqual(captured[0][0], "/v1/responses")
        self.assertEqual(captured[0][1], "Bearer test-key-only")
        self.assertEqual(captured[0][2]["tool_choice"], "required")
        self.assertFalse(captured[0][2]["parallel_tool_calls"])
        self.assertFalse(captured[0][2]["store"])
        self.assertIn("reasoning.encrypted_content", captured[0][2]["include"])
        self.assertTrue(any(i.get("type") == "reasoning" for i in captured[1][2]["input"]))
        self.assertNotIn("opaque-test-only", json.dumps(record))
        self.assertNotIn("test-key-only", json.dumps(record))
        self.assertEqual(record["trace"][0]["model_metadata"]["usage"]["total_tokens"], 15)

    def test_rejects_malformed_or_incomplete_api_responses(self):
        valid = api_response(action("search_docs", query="报销", top_k=8))
        bad_args = api_response(action("search_docs", query="报销", top_k=8))
        bad_args["output"][-1]["arguments"] = "not json"
        multiple = api_response(action("search_docs", query="报销", top_k=8))
        multiple["output"].append(multiple["output"][-1].copy())
        for response in ({}, {"status": "incomplete", "output": []},
                         {"status": "completed", "output": []}, bad_args, multiple,
                         {"status": "completed", "output": [None]}):
            with self.subTest(response=response):
                model = ResponsesModel("key", "model", transport=lambda payload: response)
                with self.assertRaises(ModelError):
                    model.decide([], 5)
        model = ResponsesModel("key", "model", transport=lambda payload: valid)
        self.assertEqual(model.decide([], 5).name, "search_docs")

    def test_safe_network_and_http_errors(self):
        exceptions = [HTTPError("https://example.test", 401, "bad", {}, io.BytesIO(b"SECRET")),
                      URLError("SECRET"), TimeoutError("SECRET")]
        for error in exceptions:
            with self.subTest(error=type(error).__name__):
                with patch("knowledge_agent.models.build_opener") as opener:
                    opener.return_value.open.side_effect = error
                    model = ResponsesModel("SECRET", "test-model")
                    record = Agent(model, knowledge()).run("报销")
                    self.assertEqual(record["result"]["status"], "model_error")
                    self.assertNotIn("SECRET", json.dumps(record))

    def test_remote_http_credentials_and_empty_configuration_rejected(self):
        for base in ("http://example.com/v1", "https://user:password@example.com/v1",
                     "https://example.com/v1?api_key=secret", "file:///tmp/model"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                ResponsesModel("key", "model", base)
        for key, model in (("", "model"), ("key", "")):
            with self.assertRaises(ValueError):
                ResponsesModel(key, model)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_unknown_tool_is_a_repairable_observation(self):
        pending = iter([api_response(action("delete_all")), api_response(
            finish("insufficient_evidence", citations=[]))])
        model = ResponsesModel("test-key", "test-model", transport=lambda payload: next(pending))
        record = Agent(model, knowledge()).run("报销")
        self.assertEqual(record["trace"][0]["observation"]["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(record["result"]["status"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
