"""증분 임베딩(Incremental Ingestion).

문서마다 매니페스트(문서 해시, 청크 해시 목록)를 Redis에 저장해 두고 다음 수집 때 비교한다.

- 문서 해시가 같으면: 아무것도 하지 않는다(임베딩 0회)
- 본문 일부가 바뀌면: 새로 생긴 청크만 임베딩하고, 사라진 청크만 Vector DB에서 지운다
- 메타데이터만 바뀌면: 임베딩 없이 payload만 갱신한다

포인트 ID를 uuid5(doc_id:chunk_hash)로 만들기 때문에 내용이 같은 청크는 위치가 바뀌어도 같은 ID를 가진다.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from redis import Redis

from app.rag.chunker import chunk_text
from app.rag.embedder import CachedEmbedder

POINT_NS = uuid.UUID("6f1c2a4e-9f1d-4c8e-8a77-1d2b3c4d5e6f")
INDEXED_FIELDS = {
    "service": qm.PayloadSchemaType.KEYWORD,
    "category": qm.PayloadSchemaType.KEYWORD,
    "doc_type": qm.PayloadSchemaType.KEYWORD,
    "doc_id": qm.PayloadSchemaType.KEYWORD,
    "updated_ts": qm.PayloadSchemaType.INTEGER,
}


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
        return {
            "doc_id": self.doc_id,
            "service": self.service,
            "category": self.category,
            "doc_type": self.doc_type,
            "updated_ts": int(ts.timestamp()),
            "source_uri": self.source_uri,
        }


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


class IncrementalIngestor:
    def __init__(self, qdrant: QdrantClient, collection: str, embedder: CachedEmbedder, redis: Redis):
        self.qdrant = qdrant
        self.collection = collection
        self.embedder = embedder
        self.redis = redis

    def ensure_collection(self) -> None:
        if self.qdrant.collection_exists(self.collection):
            return
        self.qdrant.create_collection(
            self.collection,
            vectors_config=qm.VectorParams(size=self.embedder.dim, distance=qm.Distance.COSINE),
        )
        # 메타데이터 필터가 빠르게 동작하도록 payload 인덱스를 만든다
        for name, schema in INDEXED_FIELDS.items():
            self.qdrant.create_payload_index(self.collection, field_name=name, field_schema=schema)

    def _manifest_key(self, doc_id: str) -> str:
        return f"rag:manifest:{self.collection}:{doc_id}"

    def _point_id(self, doc_id: str, chunk_hash: str) -> str:
        return str(uuid.uuid5(POINT_NS, f"{doc_id}:{chunk_hash}"))

    def ingest(self, docs: list[Document]) -> IngestReport:
        self.ensure_collection()
        report = IngestReport()
        any_change = False

        for doc in docs:
            meta = doc.metadata()
            chunks = chunk_text(doc.text)
            chunk_map = {_sha(c): c for c in chunks}  # 같은 문서 안 중복 청크 제거
            doc_hash = _sha(doc.text + json.dumps(meta, sort_keys=True))

            raw = self.redis.get(self._manifest_key(doc.doc_id))
            manifest = json.loads(raw) if raw else {"doc_hash": None, "chunks": []}
            if manifest["doc_hash"] == doc_hash:
                report.skipped_docs += 1
                continue

            any_change = True
            report.changed_docs += 1
            old_hashes = set(manifest["chunks"])
            new_hashes = set(chunk_map)
            to_embed = [h for h in chunk_map if h not in old_hashes]
            to_keep = [h for h in chunk_map if h in old_hashes]
            to_delete = old_hashes - new_hashes

            if to_embed:
                vectors = self.embedder.embed([chunk_map[h] for h in to_embed])
                points = [
                    qm.PointStruct(
                        id=self._point_id(doc.doc_id, h),
                        vector=vec,
                        payload={**meta, "chunk_hash": h, "text": chunk_map[h]},
                    )
                    for h, vec in zip(to_embed, vectors)
                ]
                self.qdrant.upsert(self.collection, points=points)

            if to_keep:  # 본문은 그대로, 메타데이터만 바뀌었을 수 있으므로 payload만 덮어쓴다
                self.qdrant.set_payload(
                    self.collection,
                    payload=meta,
                    points=[self._point_id(doc.doc_id, h) for h in to_keep],
                )

            if to_delete:
                self.qdrant.delete(
                    self.collection,
                    points_selector=qm.PointIdsList(points=[self._point_id(doc.doc_id, h) for h in to_delete]),
                )

            self.redis.set(
                self._manifest_key(doc.doc_id),
                json.dumps({"doc_hash": doc_hash, "chunks": [h for h in chunk_map]}),
            )
            report.embedded_chunks += len(to_embed)
            report.reused_chunks += len(to_keep)
            report.deleted_chunks += len(to_delete)
            report.details.append(
                {"doc_id": doc.doc_id, "embedded": len(to_embed), "reused": len(to_keep), "deleted": len(to_delete)}
            )

        if any_change:
            # 지식베이스가 바뀌면 검색 캐시 버전을 올려 이전 검색 결과 캐시를 무효화한다
            self.redis.incr(f"rag:version:{self.collection}")
        return report

    def delete_document(self, doc_id: str) -> None:
        self.qdrant.delete(
            self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
            ),
        )
        self.redis.delete(self._manifest_key(doc_id))
        self.redis.incr(f"rag:version:{self.collection}")
