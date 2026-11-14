"""임베딩 생성기 + 임베딩 캐시.

CachedEmbedder는 텍스트 해시를 키로 벡터를 Redis에 저장한다.
같은 문장(예: 여러 런북에 반복되는 공통 안내문, 반복되는 검색 질의)은 다시 임베딩하지 않는다.
"""
import hashlib
import math
import re
from array import array
from typing import Protocol

from redis import Redis


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """외부 의존성 없는 결정적 임베딩(테스트, 오프라인 데모용). 의미 검색 품질은 낮다."""

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.model_id = f"hash-{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in re.findall(r"[\w가-힣]+", text.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FastEmbedEmbedder:
    """로컬 ONNX 모델. API 비용이 들지 않는다."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self.model_id = f"fastembed:{model_name}"
        self.dim = len(next(iter(self._model.embed(["dim probe"]))))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]


class OpenAIEmbedder:
    def __init__(self, model_name: str):
        from openai import OpenAI

        self._client = OpenAI()
        self._model = model_name
        self.model_id = f"openai:{model_name}"
        self.dim = len(self.embed(["dim probe"])[0])

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]


class CachedEmbedder:
    def __init__(self, inner: Embedder, redis: Redis, ttl_sec: int = 30 * 86400):
        self.inner = inner
        self.redis = redis
        self.ttl = ttl_sec
        self.model_id = inner.model_id
        self.dim = inner.dim
        self.stats = {"hits": 0, "misses": 0}

    def _key(self, text: str) -> str:
        return f"emb:{self.model_id}:{hashlib.sha256(text.encode()).hexdigest()}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [self._key(t) for t in texts]
        cached = self.redis.mget(keys) if keys else []
        result: list[list[float] | None] = [None] * len(texts)
        miss_idx = []
        for i, raw in enumerate(cached):
            if raw is None:
                miss_idx.append(i)
            else:
                result[i] = array("f", raw).tolist()
        self.stats["hits"] += len(texts) - len(miss_idx)
        self.stats["misses"] += len(miss_idx)

        if miss_idx:
            # 같은 배치 안 중복 텍스트는 한 번만 임베딩
            unique: dict[str, list[int]] = {}
            for i in miss_idx:
                unique.setdefault(texts[i], []).append(i)
            vectors = self.inner.embed(list(unique))
            pipe = self.redis.pipeline()
            for (text, idxs), vec in zip(unique.items(), vectors):
                for i in idxs:
                    result[i] = vec
                pipe.set(self._key(text), array("f", vec).tobytes(), ex=self.ttl)
            pipe.execute()
        return result  # type: ignore[return-value]


def build_embedder(provider: str, model_name: str, redis: Redis) -> CachedEmbedder:
    if provider == "fastembed":
        inner: Embedder = FastEmbedEmbedder(model_name)
    elif provider == "openai":
        inner = OpenAIEmbedder(model_name)
    elif provider == "hash":
        inner = HashEmbedder()
    else:
        raise ValueError(f"unknown embedding provider: {provider}")
    return CachedEmbedder(inner, redis)
