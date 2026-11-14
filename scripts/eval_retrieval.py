"""2단계 검색 비교: Qdrant 벡터(1단계), pgvector 벡터, pgvector + pg_trgm 하이브리드(RRF).

지식 문서와 평가용 방해 문서(eval/retrieval/distractors)를 같은 임베딩 모델로 넣고,
에러 코드 질의 10개(eval/retrieval/queries.yaml)의 상위 3개 적중률과 검색 지연 중앙값을 잰다.
메타데이터 필터 없이 순위만 비교한다. 질의 임베딩 시간은 지연에서 뺀다(세 방식이 같은 벡터를 쓴다).

실행:
  docker compose up -d postgres && docker compose --profile eval up -d qdrant
  pip install -r eval/requirements.txt
  python -m scripts.eval_retrieval --out results/phase2_retrieval.csv
"""
import argparse
import csv
import statistics
import sys
import time
import uuid
from pathlib import Path

import yaml

from app.config import Settings
from app.db import ensure_database, make_pool, migrate, truncate_all
from app.infra import make_redis
from app.rag.chunker import chunk_text
from app.rag.embedder import build_embedder
from app.rag.ingest import IncrementalIngestor
from app.rag.loader import load_markdown_dir
from app.rag.retriever import Retriever, SearchFilter

EVAL_DIR = Path("eval/retrieval")
TOP = 3
LATENCY_REPEAT = 10  # 질의 10개 x 10회 = 100회
COLLECTION = "eval_retrieval"


def load_docs():
    docs = load_markdown_dir("knowledge")
    for d in load_markdown_dir(str(EVAL_DIR / "distractors")):
        d.doc_id = f"distractors/{d.doc_id}"
        docs.append(d)
    return docs


class QdrantVector:
    """1단계 Retriever의 검색 부분(벡터 유사도만)과 같은 질의."""

    def __init__(self, url: str, embedder, docs):
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm

        self.client = QdrantClient(location=":memory:") if url == ":memory:" else QdrantClient(url=url)
        if self.client.collection_exists(COLLECTION):
            self.client.delete_collection(COLLECTION)
        self.client.create_collection(COLLECTION, vectors_config=qm.VectorParams(size=embedder.dim, distance=qm.Distance.COSINE))
        chunks = [(d.doc_id, c) for d in docs for c in dict.fromkeys(chunk_text(d.text))]
        vectors = embedder.embed([c for _, c in chunks])
        self.client.upsert(COLLECTION, points=[
            qm.PointStruct(id=str(uuid.uuid4()), vector=v, payload={"doc_id": doc_id}) for (doc_id, _), v in zip(chunks, vectors)
        ])

    def ranked(self, vector: list[float], query: str, k: int) -> list[str]:
        points = self.client.query_points(COLLECTION, query=vector, limit=k, with_payload=True).points
        return [p.payload["doc_id"] for p in points]


class Postgres:
    def __init__(self, retriever: Retriever):
        self.retriever = retriever

    def ranked(self, vector: list[float], query: str, k: int) -> list[str]:
        return [h.doc_id for h in self.retriever._search(query, SearchFilter(), k)]


def first_rank(ranked: list[str], expected: list[str]) -> int | None:
    return next((i + 1 for i, doc_id in enumerate(ranked) if doc_id in expected), None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default="postgresql://aiops:aiops@localhost:5432/aiops_eval")
    ap.add_argument("--qdrant", default="http://localhost:6333", help=":memory:를 주면 프로세스 안 Qdrant(지연 비교에는 쓰지 않는다)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    settings = Settings(_env_file=None)
    redis = make_redis("fakeredis://")
    embedder = build_embedder(settings.embedding_provider, settings.embedding_model, redis)
    ensure_database(args.database)
    migrate(args.database)
    db = make_pool(args.database)
    truncate_all(db)

    docs = load_docs()
    IncrementalIngestor(db, embedder, redis).ingest(docs)
    queries = yaml.safe_load((EVAL_DIR / "queries.yaml").read_text())
    vectors = embedder.embed([q["q"] for q in queries])

    modes = {
        "qdrant_vector": QdrantVector(args.qdrant, embedder, docs),
        "pg_vector": Postgres(Retriever(db, embedder, redis, cache_ttl=0, keyword=False)),
        "pg_hybrid": Postgres(Retriever(db, embedder, redis, cache_ttl=0, keyword=True)),
    }

    rows, summary = [], {}
    for name, mode in modes.items():
        hits, latencies = 0, []
        for q, vec in zip(queries, vectors):
            ranked = mode.ranked(vec, q["q"], 10)
            rank = first_rank(ranked, q["expected"])
            hits += rank is not None and rank <= TOP
            rows.append({"mode": name, "query": q["q"], "expected": " ".join(q["expected"]), "rank": rank or "",
                         "hit_at_3": rank is not None and rank <= TOP, "top3": " ".join(ranked[:TOP])})
        for _ in range(LATENCY_REPEAT):
            for q, vec in zip(queries, vectors):
                t = time.perf_counter()
                mode.ranked(vec, q["q"], TOP)
                latencies.append((time.perf_counter() - t) * 1000)
        summary[name] = {"hit_at_3": f"{hits}/{len(queries)}", "latency_ms_p50": round(statistics.median(latencies), 2),
                         "latency_ms_p95": round(statistics.quantiles(latencies, n=20)[18], 2)}

    print(f"문서 {len(docs)}개(지식 {len(docs) - len(list((EVAL_DIR / 'distractors').glob('*.md')))}, 방해 "
          f"{len(list((EVAL_DIR / 'distractors').glob('*.md')))}), 질의 {len(queries)}개, 임베딩 {embedder.model_id}", file=sys.stderr)
    for name, s in summary.items():
        print(f"{name:14} 상위3 적중 {s['hit_at_3']:>5}  지연 p50 {s['latency_ms_p50']}ms p95 {s['latency_ms_p95']}ms")
    if args.out:
        with args.out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    db.close()


if __name__ == "__main__":
    main()
