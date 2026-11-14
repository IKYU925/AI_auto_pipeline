"""메타데이터 필터 우선 하이브리드 검색 + 검색 결과 캐시.

서비스, 카테고리, 문서 종류, 날짜로 후보를 먼저 좁힌 뒤, 그 안에서 두 가지 순위를 만든다.

- 벡터 순위(pgvector 코사인 거리): 표현이 달라도 뜻이 비슷한 문서
- 키워드 순위(pg_trgm word_similarity): `PG_API_KEY`, `PaymentTimeout` 같은 에러 코드, 식별자가 그대로 들어간 문서

두 순위는 RRF(Reciprocal Rank Fusion, 1 / (60 + 순위)의 합)로 합친다. 점수 척도가 다른 두 검색을 정규화 없이 섞을 수 있다.
"""
import hashlib
import json
from dataclasses import asdict, dataclass

from psycopg_pool import ConnectionPool
from redis import Redis

from app.rag.embedder import CachedEmbedder
from app.rag.ingest import to_pgvector

RRF_K = 60
CANDIDATES = 20  # 순위마다 RRF에 넣는 후보 수


@dataclass
class SearchFilter:
    service: str | None = None
    categories: list[str] | None = None
    doc_types: list[str] | None = None
    since_days: int | None = None
    include_common: bool = True  # 서비스 공통 문서(service=common)도 포함

    def to_sql(self) -> tuple[str, dict]:
        where, params = ["true"], {}
        if self.service:
            where.append("service = ANY(%(services)s)")
            params["services"] = [self.service, "common"] if self.include_common else [self.service]
        if self.categories:
            where.append("category = ANY(%(categories)s)")
            params["categories"] = self.categories
        if self.doc_types:
            where.append("doc_type = ANY(%(doc_types)s)")
            params["doc_types"] = self.doc_types
        if self.since_days:
            where.append("updated_at >= now() - make_interval(days => %(since_days)s)")
            params["since_days"] = self.since_days
        return " AND ".join(where), params


@dataclass
class Hit:
    doc_id: str
    score: float
    text: str
    service: str
    category: str
    doc_type: str


class Retriever:
    def __init__(self, db: ConnectionPool, embedder: CachedEmbedder, redis: Redis, cache_ttl: int, keyword: bool = True):
        self.db = db
        self.embedder = embedder
        self.redis = redis
        self.cache_ttl = cache_ttl
        self.keyword = keyword
        self.stats = {"cache_hits": 0, "searches": 0, "relaxed": 0}

    def _cache_key(self, query: str, flt: SearchFilter, top_k: int) -> str:
        version = self.redis.get("rag:version") or b"0"
        basis = json.dumps(
            {"q": query, "f": asdict(flt), "k": top_k, "m": self.embedder.model_id, "kw": self.keyword, "v": version.decode()},
            sort_keys=True,
        )
        return f"rag:q:{hashlib.sha256(basis.encode()).hexdigest()}"

    def _search(self, query: str, flt: SearchFilter, top_k: int) -> list[Hit]:
        where, params = flt.to_sql()
        params |= {"v": to_pgvector(self.embedder.embed([query])[0]), "q": query, "n": CANDIDATES, "k": top_k, "rrf": RRF_K}
        keyword = f"""
            SELECT id, row_number() OVER (ORDER BY word_similarity(%(q)s, content) DESC) AS r
            FROM documents WHERE {where} AND word_similarity(%(q)s, content) > 0
            ORDER BY word_similarity(%(q)s, content) DESC LIMIT %(n)s""" if self.keyword else "SELECT NULL::bigint AS id, NULL::bigint AS r WHERE false"
        sql = f"""
            WITH vec AS (
              SELECT id, row_number() OVER (ORDER BY embedding <=> %(v)s::vector) AS r
              FROM documents WHERE {where} ORDER BY embedding <=> %(v)s::vector LIMIT %(n)s
            ), kw AS ({keyword})
            SELECT d.doc_id, d.content, d.service, d.category, d.doc_type,
                   coalesce(1.0 / (%(rrf)s + vec.r), 0) + coalesce(1.0 / (%(rrf)s + kw.r), 0) AS score
            FROM documents d LEFT JOIN vec ON vec.id = d.id LEFT JOIN kw ON kw.id = d.id
            WHERE vec.id IS NOT NULL OR kw.id IS NOT NULL
            ORDER BY score DESC, d.id LIMIT %(k)s"""
        with self.db.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        self.stats["searches"] += 1
        return [
            Hit(doc_id=r["doc_id"], score=round(float(r["score"]), 4), text=r["content"], service=r["service"],
                category=r["category"], doc_type=r["doc_type"])
            for r in rows
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
