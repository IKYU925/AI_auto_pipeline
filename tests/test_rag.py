from datetime import datetime, timedelta, timezone

from app.rag.ingest import Document
from app.rag.retriever import SearchFilter


def _doc(text: str, **kw) -> Document:
    base = dict(doc_id="runbook.md", service="checkout", category="code_bug", doc_type="runbook",
                updated_at=datetime.now(timezone.utc))
    base.update(kw)
    return Document(text=text, **base)


BODY = "# 제목\n\n첫 번째 문단은 장애 증상이다.\n\n## 원인\n\n두 번째 문단은 원인이다.\n\n## 조치\n\n세 번째 문단은 조치다."


def test_unchanged_document_is_not_reembedded(container):
    first = container.ingestor.ingest([_doc(BODY)])
    assert first.embedded_chunks == 3
    second = container.ingestor.ingest([_doc(BODY)])
    assert second.skipped_docs == 1 and second.embedded_chunks == 0


def test_only_changed_chunk_is_reembedded(container):
    container.ingestor.ingest([_doc(BODY)])
    edited = BODY.replace("두 번째 문단은 원인이다.", "두 번째 문단은 수정된 원인이다.")
    report = container.ingestor.ingest([_doc(edited)])
    assert (report.embedded_chunks, report.reused_chunks, report.deleted_chunks) == (1, 2, 1)
    with container.db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == 3


def test_metadata_only_change_updates_payload_without_embedding(container):
    container.ingestor.ingest([_doc(BODY)])
    report = container.ingestor.ingest([_doc(BODY, category="config")])
    assert report.embedded_chunks == 0 and report.reused_chunks == 3
    hits = container.retriever.search("원인", SearchFilter(service="checkout", categories=["config"]), top_k=5)
    assert hits and all(h.category == "config" for h in hits)


def test_metadata_filter_scopes_search_before_similarity(container):
    old = datetime.now(timezone.utc) - timedelta(days=400)
    container.ingestor.ingest([
        _doc("결제 타임아웃 원인과 조치", doc_id="pay.md", service="payment", category="infra"),
        _doc("주문 타임아웃 원인과 조치", doc_id="order.md", service="checkout", category="infra"),
        _doc("오래된 주문 타임아웃 문서", doc_id="old.md", service="checkout", category="infra", updated_at=old),
    ])
    hits = container.retriever.search("타임아웃", SearchFilter(service="checkout", since_days=180), top_k=5)
    assert {h.doc_id for h in hits} == {"order.md"}


def test_query_cache_and_invalidation(container):
    container.ingestor.ingest([_doc(BODY)])
    flt = SearchFilter(service="checkout")
    container.retriever.search("원인", flt)
    container.retriever.search("원인", flt)
    assert container.retriever.stats["cache_hits"] == 1
    # 문서가 바뀌면 캐시 버전이 올라가서 다시 검색한다
    container.ingestor.ingest([_doc(BODY + "\n\n## 추가\n\n새 문단")])
    container.retriever.search("원인", flt)
    assert container.retriever.stats["cache_hits"] == 1


def test_embedding_cache_shared_across_documents(container):
    container.ingestor.ingest([_doc("공통 안내 문단", doc_id="a.md")])
    container.ingestor.ingest([_doc("공통 안내 문단", doc_id="b.md")])
    assert container.embedder.stats["hits"] == 1


def test_keyword_rank_finds_exact_error_code(container):
    container.ingestor.ingest([
        _doc("결제 승인 요청이 늦어지면 타임아웃 설정을 확인한다.", doc_id="vague.md"),
        _doc("`PG_TIMEOUT_SEC` 기본값은 30이다.", doc_id="code.md"),
    ])
    flt = SearchFilter(service="checkout")
    hybrid = container.retriever._search("PG_TIMEOUT_SEC", flt, top_k=1)
    assert [h.doc_id for h in hybrid] == ["code.md"]
    assert hybrid[0].score > 1 / 61, "벡터와 키워드 양쪽 순위에서 점수를 받는다"


def test_embedding_model_change_reembeds(container):
    container.ingestor.ingest([_doc(BODY)])
    with container.db.connection() as conn:
        conn.execute("UPDATE documents SET embed_model = 'old-model'")
    report = container.ingestor.ingest([_doc(BODY)])
    assert (report.embedded_chunks, report.reused_chunks, report.deleted_chunks) == (3, 0, 0)
