"""DashScope native dense embeddings. Standard library only; no key in logs."""
from __future__ import annotations

import hashlib
from http.client import HTTPException
import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from .models import NoRedirect
from .http_json import read_json, RequestDeadlineExceeded

DEFAULT_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
DIMENSIONS = {64, 128, 256, 512, 768, 1024, 1536, 2048}


class EmbeddingError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable

    def safe_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


def normalize(vector: list, dimensions: int | None = None) -> list[float]:
    if (not isinstance(vector, list) or not vector
            or (dimensions is not None and len(vector) != dimensions)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
        raise EmbeddingError("INVALID_VECTOR", "向量格式、维度或数值无效")
    norm = math.sqrt(math.fsum(v * v for v in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise EmbeddingError("INVALID_VECTOR", "向量必须具有有限的非零范数")
    return [v / norm for v in vector]


class DashScopeEmbedder:
    provider = "dashscope"
    evidence_kind = "remote_embedding_api"

    def __init__(self, api_key: str = "", model: str = "text-embedding-v4",
                 dimensions: int = 1024, url: str = DEFAULT_URL,
                 timeout: float = 15, transport=None):
        if model != "text-embedding-v4":
            raise ValueError("本适配器使用 text-embedding-v4；请检查 DASHSCOPE_EMBEDDING_MODEL")
        if type(dimensions) is not int or dimensions not in DIMENSIONS:
            raise ValueError("DASHSCOPE_EMBEDDING_DIMENSIONS 不受 text-embedding-v4 支持")
        parsed = urlsplit(url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parsed.hostname or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or "{" in url or "}" in url):
            raise ValueError("DASHSCOPE_EMBEDDING_URL须为有效HTTPS地址，不得含凭证/查询参数/占位符；本机测试允许HTTP")
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("DASHSCOPE_TIMEOUT须在0到60秒之间")
        self.api_key, self.model, self.dimensions = api_key, model, dimensions
        self.url, self.timeout = url, timeout
        self.transport = transport or self._post
        self.requests: list[dict] = []
        identity = json.dumps([self.provider, model, dimensions, url, "native-document-query-v1"])
        self.cache_key = hashlib.sha256(identity.encode()).hexdigest()

    @classmethod
    def from_env(cls) -> "DashScopeEmbedder":
        return cls(os.getenv("DASHSCOPE_API_KEY", ""),
                   os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4"),
                   int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSIONS", "1024")),
                   os.getenv("DASHSCOPE_EMBEDDING_URL", DEFAULT_URL),
                   float(os.getenv("DASHSCOPE_TIMEOUT", "15")))

    def _post(self, payload: dict) -> dict:
        request = Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self.api_key,
                                   "Content-Type": "application/json"}, method="POST")
        try:
            return read_json(request, build_opener(NoRedirect()), self.timeout)
        except HTTPError as exc:
            raise EmbeddingError("HTTP_" + str(exc.code), f"DashScope HTTP {exc.code}；请检查地域、权限、额度及配置",
                                 exc.code == 429 or exc.code >= 500) from None
        except RequestDeadlineExceeded:
            raise EmbeddingError("REQUEST_DEADLINE", f"DashScope请求达到 {self.timeout:g} 秒总时限", True) from None
        except (URLError, TimeoutError, OSError, HTTPException):
            raise EmbeddingError("NETWORK_ERROR", "DashScope网络错误或超时", True) from None
        except (ValueError, UnicodeError):
            raise EmbeddingError("PROTOCOL_ERROR", "DashScope返回无效JSON") from None

    def embed(self, texts: list[str], text_type: str) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingError("CONFIG_MISSING", "未配置 DASHSCOPE_API_KEY，向量检索不可用")
        if text_type not in {"document", "query"} or not isinstance(texts, list) or not texts or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise ValueError("embedding输入须为非空文本列表，text_type为document或query")
        vectors = []
        for start in range(0, len(texts), 10):
            batch = texts[start:start + 10]
            payload = {"model": self.model, "input": {"texts": batch},
                       "parameters": {"text_type": text_type, "dimension": self.dimensions,
                                      "output_type": "dense"}}
            response = self.transport(payload)
            try:
                items = response["output"]["embeddings"]
                if not isinstance(items, list) or len(items) != len(batch):
                    raise ValueError
                ordered = {}
                for item in items:
                    # Native gateway versions use index or legacy text_index.
                    index = item.get("index", item.get("text_index"))
                    if (type(index) is not int or index not in range(len(batch)) or index in ordered
                            or ("index" in item and "text_index" in item and item["index"] != item["text_index"])):
                        raise ValueError
                    ordered[index] = normalize(item["embedding"], self.dimensions)
                vectors.extend(ordered[i] for i in range(len(batch)))
                usage = response.get("usage") or {}
                tokens = usage.get("total_tokens")
                self.requests.append({"request_id": response.get("request_id"), "text_type": text_type,
                                      "batch_size": len(batch),
                                      "total_tokens": tokens if type(tokens) is int else None})
            except (KeyError, TypeError, ValueError, AttributeError):
                raise EmbeddingError("PROTOCOL_ERROR", "DashScope向量条数或索引无效，未采用该响应") from None
        return vectors
