"""증분 임베딩(Incremental Ingestion).

documents 테이블의 (doc_id, chunk_hash) 행이 매니페스트 역할을 한다. 다음 수집 때 이 행들과 비교한다.

- 청크 해시와 메타데이터가 같으면: 아무것도 하지 않는다(임베딩 0회)
- 본문 일부가 바뀌면: 새로 생긴 청크만 임베딩하고, 사라진 청크 행만 지운다
- 메타데이터만 바뀌면: 임베딩 없이 메타데이터 칼럼만 갱신한다
- 임베딩 모델이 바뀌면: 다른 모델로 만든 행은 없는 것으로 보고 다시 임베딩한다
"""
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from psycopg_pool import ConnectionPool
from redis import Redis

from app.rag.chunker import chunk_text
from app.rag.embedder import CachedEmbedder

# migrations/versions/0001_initial.py의 documents.embedding vector(384)
EMBEDDING_DIM = 384


@dataclass
class Document:
    doc_id: str
    text: str
    service: str = "common"
    category: str = "general"
    doc_type: str = "runbook"
    updated_at: datetime | None = None
    source_uri: str | None = None

    def metadata(self) -> dict:
        ts = self.updated_at or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        # 1단계 매니페스트처럼 초 단위로 비교한다
        return {"service": self.service, "category": self.category, "doc_type": self.doc_type,
                "updated_at": ts.replace(microsecond=0)}


@dataclass
class IngestReport:
    skipped_docs: int = 0
    changed_docs: int = 0
    embedded_chunks: int = 0
    reused_chunks: int = 0
    deleted_chunks: int = 0
    details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


class IncrementalIngestor:
    def __init__(self, db: ConnectionPool, embedder: CachedEmbedder, redis: Redis):
        if embedder.dim != EMBEDDING_DIM:
            raise ValueError(f"임베딩 차원 {embedder.dim}이 documents.embedding({EMBEDDING_DIM})과 다르다. 마이그레이션이 필요하다")
        self.db = db
        self.embedder = embedder
        self.redis = redis

    def ingest(self, docs: list[Document]) -> IngestReport:
        report = IngestReport()
        model = self.embedder.model_id
        with self.db.connection() as conn:
            for doc in docs:
                meta = doc.metadata()
                chunk_map = {_sha(c): c for c in chunk_text(doc.text)}  # 같은 문서 안 중복 청크 제거
                rows = conn.execute(
                    "SELECT chunk_hash, service, category, doc_type, updated_at, embed_model FROM documents WHERE doc_id = %s",
                    (doc.doc_id,),
                ).fetchall()
                current = {r["chunk_hash"] for r in rows if r["embed_model"] == model}
                same_meta = all({k: r[k] for k in meta} == meta for r in rows)
                if current == set(chunk_map) and len(rows) == len(current) and same_meta:
                    report.skipped_docs += 1
                    continue

                report.changed_docs += 1
                to_embed = [h for h in chunk_map if h not in current]
                to_keep = [h for h in chunk_map if h in current]
                to_delete = {r["chunk_hash"] for r in rows} - set(to_keep)

                if to_delete:
                    conn.execute("DELETE FROM documents WHERE doc_id = %s AND chunk_hash = ANY(%s)", (doc.doc_id, list(to_delete)))
                if to_keep:
                    conn.execute(
                        """UPDATE documents SET service = %(service)s, category = %(category)s, doc_type = %(doc_type)s,
                             updated_at = %(updated_at)s WHERE doc_id = %(doc_id)s""",
                        {**meta, "doc_id": doc.doc_id},
                    )
                if to_embed:
                    vectors = self.embedder.embed([chunk_map[h] for h in to_embed])
                    with conn.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO documents (doc_id, chunk_hash, content, service, category, doc_type, updated_at,
                                 embed_model, embedding)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
                            [(doc.doc_id, h, chunk_map[h], meta["service"], meta["category"], meta["doc_type"],
                              meta["updated_at"], model, to_pgvector(v)) for h, v in zip(to_embed, vectors)],
                        )
                # 이 모델로 다시 만든 청크는 삭제 수에서 뺀다(행은 지웠다가 새로 넣었다)
                deleted = len(to_delete - set(to_embed))
                report.embedded_chunks += len(to_embed)
                report.reused_chunks += len(to_keep)
                report.deleted_chunks += deleted
                report.details.append({"doc_id": doc.doc_id, "embedded": len(to_embed), "reused": len(to_keep), "deleted": deleted})

        if report.changed_docs:
            # 지식베이스가 바뀌면 검색 캐시 버전을 올려 이전 검색 결과 캐시를 무효화한다
            self.redis.incr("rag:version")
        return report

    def delete_document(self, doc_id: str) -> None:
        with self.db.connection() as conn:
            conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
        self.redis.incr("rag:version")
