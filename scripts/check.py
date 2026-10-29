"""Run tests and save their actual result for reviewers. No third-party runner."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import platform
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    started = time.monotonic()
    log = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    output = log.getvalue()
    print(output)
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "python": platform.python_version(), "platform": platform.system(),
              "command": "python scripts/check.py", "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "passed": result.wasSuccessful(),
              "elapsed_seconds": round(time.monotonic() - started, 4),
              "api_test_boundary": "HTTP/API测试使用本机假服务或模拟返回，不是真实模型运行。"}
    directory = ROOT / "examples"
    directory.mkdir(exist_ok=True)
    (directory / "test-results.txt").write_text(output, encoding="utf-8")
    (directory / "test-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
