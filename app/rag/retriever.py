"""메타데이터 필터 우선 검색 + 검색 결과 캐시.

전체 벡터를 대상으로 유사도 검색을 하면 다른 서비스의 비슷한 문서가 섞여 들어오고 비용도 커진다.
서비스, 카테고리, 문서 종류, 날짜로 후보를 먼저 좁힌 뒤(Qdrant payload filter)
그 안에서만 벡터 유사도 검색을 한다.
"""
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from redis import Redis

from app.rag.embedder import CachedEmbedder


@dataclass
class SearchFilter:
    service: str | None = None
    categories: list[str] | None = None
    doc_types: list[str] | None = None
    since_days: int | None = None
    include_common: bool = True  # 서비스 공통 문서(service=common)도 포함

    def to_qdrant(self) -> qm.Filter | None:
        must: list = []
        if self.service:
            services = [self.service, "common"] if self.include_common else [self.service]
            must.append(qm.FieldCondition(key="service", match=qm.MatchAny(any=services)))
        if self.categories:
            must.append(qm.FieldCondition(key="category", match=qm.MatchAny(any=self.categories)))
        if self.doc_types:
            must.append(qm.FieldCondition(key="doc_type", match=qm.MatchAny(any=self.doc_types)))
        if self.since_days:
            since = datetime.now(timezone.utc) - timedelta(days=self.since_days)
            must.append(qm.FieldCondition(key="updated_ts", range=qm.Range(gte=int(since.timestamp()))))
        return qm.Filter(must=must) if must else None


@dataclass
class Hit:
    doc_id: str
    score: float
    text: str
    service: str
    category: str
    doc_type: str
    source_uri: str | None = None


class Retriever:
    def __init__(self, qdrant: QdrantClient, collection: str, embedder: CachedEmbedder, redis: Redis, cache_ttl: int):
        self.qdrant = qdrant
        self.collection = collection
        self.embedder = embedder
        self.redis = redis
        self.cache_ttl = cache_ttl
        self.stats = {"cache_hits": 0, "searches": 0, "relaxed": 0}

    def _cache_key(self, query: str, flt: SearchFilter, top_k: int) -> str:
        version = self.redis.get(f"rag:version:{self.collection}") or b"0"
        basis = json.dumps(
            {"q": query, "f": asdict(flt), "k": top_k, "m": self.embedder.model_id, "v": version.decode()},
            sort_keys=True,
        )
        return f"rag:q:{hashlib.sha256(basis.encode()).hexdigest()}"

    def _search(self, query: str, flt: SearchFilter, top_k: int) -> list[Hit]:
        if not self.qdrant.collection_exists(self.collection):
            return []
        vector = self.embedder.embed([query])[0]
        res = self.qdrant.query_points(
            self.collection,
            query=vector,
            query_filter=flt.to_qdrant(),
            limit=top_k,
            with_payload=True,
        ).points
        self.stats["searches"] += 1
        return [
            Hit(
                doc_id=p.payload["doc_id"],
                score=round(p.score, 4),
                text=p.payload["text"],
                service=p.payload["service"],
                category=p.payload["category"],
                doc_type=p.payload["doc_type"],
                source_uri=p.payload.get("source_uri"),
            )
            for p in res
        ]

    def search(self, query: str, flt: SearchFilter, top_k: int = 5) -> list[Hit]:
        key = self._cache_key(query, flt, top_k)
        cached = self.redis.get(key)
        if cached:
            self.stats["cache_hits"] += 1
            return [Hit(**h) for h in json.loads(cached)]

        hits = self._search(query, flt, top_k)
        if not hits and (flt.categories or flt.since_days):
            # 필터가 너무 좁아서 0건이면 서비스 조건만 남기고 한 번 더 찾는다
            self.stats["relaxed"] += 1
            hits = self._search(query, SearchFilter(service=flt.service), top_k)

        self.redis.set(key, json.dumps([asdict(h) for h in hits]), ex=self.cache_ttl)
        return hits
