from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_agent.config import load_env
from knowledge_agent.evaluation import ROOT
from knowledge_agent.models import DeepSeekModel


class ConfigurationTests(unittest.TestCase):
    def test_env_file_is_data_not_executable_and_shell_wins(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('# comment\nDEEPSEEK_API_KEY="$(not-executed)"\nDEEPSEEK_MODEL=from-file\n', encoding="utf-8")
            os.environ["DEEPSEEK_MODEL"] = "from-shell"
            load_env(path)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "$(not-executed)")
            self.assertEqual(os.environ["DEEPSEEK_MODEL"], "from-shell")

    def test_env_parse_errors_do_not_echo_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for invalid in ("not valid SECRET", "PATH=SECRET", 'DEEPSEEK_API_KEY="SECRET'):
                with self.subTest(invalid=invalid):
                    path.write_text(invalid, encoding="utf-8")
                    with self.assertRaises(ValueError) as caught:
                        load_env(path)
                    self.assertNotIn("SECRET", str(caught.exception))

    def test_deepseek_environment_defaults_and_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                DeepSeekModel.from_env()
            os.environ["DEEPSEEK_API_KEY"] = "test-key"
            model = DeepSeekModel.from_env()
            self.assertEqual(model.model, "deepseek-flash")
            self.assertEqual(model.url, "https://api.deepseek.com/responses")

    def test_cli_offline_entrypoint_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run.json"
            process = subprocess.run([sys.executable, str(ROOT / "run.py"), "ask", "报销期限多久？",
                                      "--provider", "mock", "--output", str(output)],
                                     cwd=directory, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(record["result"]["status"], "needs_clarification")
            self.assertEqual(record["evidence_kind"], "mock_flow_only")

    def test_cli_budget_exhaustion_is_nonzero_and_has_json_result(self):
        process = subprocess.run([sys.executable, str(ROOT / "run.py"), "ask", "报销期限多久？",
                                  "--provider", "mock", "--max-steps", "1"],
                                 capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["result"]["status"], "step_limit")


if __name__ == "__main__":
    unittest.main()
