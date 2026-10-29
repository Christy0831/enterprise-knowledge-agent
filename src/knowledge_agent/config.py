"""Minimal .env reader: allowlisted configuration, no shell evaluation or logging."""
import os
from pathlib import Path
import re

ALLOWED = {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL",
           "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError(f".env 第{line_number}行格式错误，应为 KEY=value")
        if name not in ALLOWED:
            raise ValueError(f".env 第{line_number}行含未支持的配置项")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f".env 第{line_number}行引号未闭合")
            value = value[1:-1]
        os.environ.setdefault(name, value)
