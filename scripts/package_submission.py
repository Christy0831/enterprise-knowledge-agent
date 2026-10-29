"""Package one committed Git snapshot and generate ready-to-paste form text."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/Christy0831/enterprise-knowledge-agent"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    git = shutil.which("git")
    if not git:
        portable = Path(os.getenv("LOCALAPPDATA", "")) / "CodexTools/MinGit-2.56.0.4/cmd/git.exe"
        git = str(portable) if portable.is_file() else None
    if not git:
        raise SystemExit("Git is required to package a committed snapshot.")

    def command(*args):
        return subprocess.run([git, *args], cwd=ROOT, capture_output=True, check=True).stdout

    if command("status", "--porcelain").strip():
        raise SystemExit("Commit all source changes before packaging; output must match the final SHA.")
    sha = command("rev-parse", "HEAD").decode().strip()
    with zipfile.ZipFile(io.BytesIO(command("archive", "--format=zip", sha))) as source:
        files = {name: source.read(name) for name in source.namelist() if not name.endswith("/")}
    required = ["README.md", "SUBMISSION.md", "test.md", ".env.example", "pyproject.toml",
                "run.py", "docs/10-submission-report.md", "submission/form-text.template.txt",
                "frontend/package-lock.json", "examples/deepseek-hybrid/report.json",
                "examples/test-results.json", "examples/ui/browser-tests.json"]
    required += [f"docs/{name}" for name in ("01-requirements.md", "02-solution.md", "03-evaluation.md",
                                           "04-ai-usage.md", "05-retrospective.md")]
    if set(required) - files.keys():
        raise SystemExit("Required submission files are missing from the committed snapshot.")
    for name in files:
        parts = PurePosixPath(name).parts
        if (name.startswith("/") or ".." in parts or any(part in {
                ".git", "node_modules", ".cache", ".venv", "__pycache__", "dist", "submission-output"
        } for part in parts) or any(part.startswith(".env") and part != ".env.example" for part in parts)):
            raise SystemExit("Excluded local/private file appears in snapshot: " + name)
    # Inspect local key values without printing or putting them in the archive.
    secrets = []
    environment_file = ROOT / ".env"
    if environment_file.is_file():
        for line in environment_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                value = value.strip().strip("\"'")
                if key.strip().endswith("API_KEY") and len(value) >= 12:
                    secrets.append(value.encode())
    for name, content in files.items():
        if any(secret in content for secret in secrets):
            raise SystemExit("Local API key found in committed file: " + name)
    form = files["submission/form-text.template.txt"].decode("utf-8").replace("{{COMMIT_SHA}}", sha)
    if "{{" in form:
        raise SystemExit("Unresolved placeholder in form text.")
    manifest = {"commit_sha": sha, "repository": REPOSITORY, "source": "git archive HEAD",
                "files": {"enterprise-knowledge-agent/" + name: hashlib.sha256(content).hexdigest()
                          for name, content in sorted(files.items())}}
    manifest["files"]["提交说明.txt"] = hashlib.sha256(form.encode()).hexdigest()
    output = ROOT / "submission-output"
    output.mkdir(exist_ok=True)
    archive = output / f"enterprise-knowledge-agent-题目3-{sha[:12]}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, content in sorted(files.items()):
            bundle.writestr("enterprise-knowledge-agent/" + name, content)
        bundle.writestr("提交说明.txt", form.encode())
        bundle.writestr("PACKAGE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    if archive.stat().st_size >= 100_000_000:
        raise SystemExit("ZIP exceeds the upload limit of 100 MB.")
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise SystemExit("ZIP integrity check failed.")
        for name, digest in manifest["files"].items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != digest:
                raise SystemExit("Archive hash mismatch: " + name)
    text_file = output / "提交框填写内容.txt"
    text_file.write_text(form, encoding="utf-8")
    print(json.dumps({"commit_sha": sha, "archive": str(archive), "form_text": str(text_file),
                      "archive_bytes": archive.stat().st_size, "source_files": len(files),
                      "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                      "secret_matches": 0, "integrity_verified": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
