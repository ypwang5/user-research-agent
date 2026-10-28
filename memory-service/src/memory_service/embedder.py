import hashlib
import math
from typing import Protocol

import httpx


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class DashScopeEmbedder:
    """通义千问 text-embedding（OpenAI 兼容接口）。"""

    BATCH = 10  # DashScope v4 单次最多 10 条

    def __init__(self, api_key: str, base_url: str, model: str, dim: int):
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 未配置")
        self.dim = dim
        self.model = model
        # 国内接口，不走本机代理
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
            trust_env=False,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = texts[i : i + self.BATCH]
            r = self._http.post(
                "/embeddings",
                json={"model": self.model, "input": batch, "dimensions": self.dim},
            )
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(_normalize(d["embedding"]) for d in data)
        return out


class FakeEmbedder:
    """字符二元组哈希成向量。字面越像相似度越高，离线测试去重逻辑用，不懂语义。"""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = []
        for t in texts:
            v = [0.0] * self.dim
            s = "".join(t.split())
            for a, b in zip(s, s[1:]):
                h = int(hashlib.md5((a + b).encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            vecs.append(_normalize(v))
        return vecs


def build_embedder(settings) -> Embedder:
    if settings.embed_provider == "fake":
        return FakeEmbedder(settings.embed_dim)
    return DashScopeEmbedder(
        settings.dashscope_api_key,
        settings.dashscope_base_url,
        settings.embed_model,
        settings.embed_dim,
    )
