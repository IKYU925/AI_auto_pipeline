"""API 키, Redis, Qdrant 없이 전체 파이프라인을 한 프로세스에서 돌려보는 데모.

실행: python -m scripts.demo_offline

1) 지식 문서 증분 임베딩(두 번째 수집은 임베딩 0회)
2) Slack(n8n) -> FastAPI 장애 접수, 중복 알림 제거
3) 워커가 큐에서 꺼내 LangGraph 실행: 분류 -> RAG -> 원인 분석 -> 수정안 -> Diff -> 영향도 -> 테스트 선별 -> 실행
   첫 수정안은 회귀 테스트에서 실패하고, 실패 로그를 받은 두 번째 수정안이 통과한다
4) 같은 에러 재발 시 분류 캐시 적중 + 검증된 수정안 재사용(테스트만 다시 돌림)
5) 인프라 장애는 고성능 모델 없이 런북 안내로 종료
"""
import json
import tempfile

from fastapi.testclient import TestClient

from app.config import Settings
from app.container import Container
from app.llm.fake import fake_model_factory
from app.main import create_app
from app.rag.loader import load_markdown_dir
from app.worker import process_message
from scripts.demo_responder import demo_responder

STACK = """Traceback (most recent call last):
  File "/app/shop/checkout.py", line 8, in checkout_summary
    "average_unit_price": cart.average_unit_price(),
  File "/app/shop/cart.py", line 28, in average_unit_price
    return unit_price(self.total(), quantity)
  File "/app/shop/pricing.py", line 13, in unit_price
    return round(total / quantity, 2)
ZeroDivisionError: float division by zero"""


def offline_settings(**overrides) -> Settings:
    base = dict(
        redis_url="fakeredis://",
        qdrant_url=":memory:",
        embedding_provider="hash",
        light_model="fake:light",
        heavy_model="fake:heavy",
        model_prices={"fake:light": [1.0, 5.0], "fake:heavy": [5.0, 25.0]},
        target_repo_path="sample_repo",
        artifacts_dir=tempfile.mkdtemp(prefix="aiauto-artifacts-"),
        automation_token="demo-token",
    )
    base.update(overrides)
    # 개발자 .env(실제 Slack 토큰, OPT_* 등)가 데모와 테스트에 섞이지 않게 읽지 않는다
    return Settings(_env_file=None, **base)


def section(title: str) -> None:
    print(f"\n{'=' * 10} {title} {'=' * 10}")


def drain(c: Container) -> list[dict]:
    results = []
    for msg_id, payload in c.queue.consume("demo-worker", count=10, block_ms=100):
        results.append(process_message(c, msg_id, payload))
    return results


def main() -> None:
    calls: list = []
    c = Container(offline_settings(), model_factory=fake_model_factory(demo_responder, calls))
    client = TestClient(create_app(c))
    headers = {"X-Automation-Token": "demo-token"}

    section("1. 지식 문서 증분 임베딩")
    docs = load_markdown_dir("knowledge")
    print("첫 수집 :", json.dumps(c.ingestor.ingest(docs).to_dict(), ensure_ascii=False))
    print("재수집  :", json.dumps(c.ingestor.ingest(docs).to_dict(), ensure_ascii=False))

    section("2. 장애 접수 (n8n -> FastAPI)")
    incident = {
        "channel": "C-INCIDENT", "thread_ts": "1731340000.000100", "service": "checkout",
        "severity": "critical", "message": "500 on /orders/summary order_id=88123: ZeroDivisionError",
        "stacktrace": STACK,
    }
    r1 = client.post("/api/v1/incidents", json=incident, headers=headers).json()
    r2 = client.post(
        "/api/v1/incidents", json={**incident, "message": incident["message"].replace("88123", "88124")}, headers=headers
    ).json()
    print("첫 알림   :", r1)
    print("중복 알림 :", r2)

    section("3. 워커 실행 (LangGraph)")
    final = drain(c)[0]
    print(f"결과: {final['outcome']}, 수정 시도 {final['attempts']}회")
    for i, h in enumerate(final.get("history", []), 1):
        print(f"  실패한 시도 {i}: {h['failure'].splitlines()[0]}")
    print("영향 범위   :", final["impact"]["changed_modules"], "->", final["impact"]["impacted"])
    print("테스트 선별 :", final["test_selection"]["reason"], final["test_selection"]["tests"])
    print("LLM 호출    :", calls)
    print("\n--- Slack 회신 내용 ---\n" + final["report"])

    section("4. 같은 에러 재발(배포 전) -> 분류 캐시 + 검증된 수정안 재사용")
    c.redis.delete(f"dedupe:{r1['fingerprint']}")  # 중복 제거 TTL이 지났다고 가정
    calls.clear()
    client.post("/api/v1/incidents", json={**incident, "message": incident["message"].replace("88123", "90001")}, headers=headers)
    final2 = drain(c)[0]
    print("결과     :", final2["outcome"], "/ 재사용:", final2.get("reused_fix"), "/", final2["test_result"]["summary"])
    print("LLM 호출 :", calls, "(빈 리스트 = LLM 0회)")
    print("비용     :", final2["cost"])

    section("5. 인프라 장애 -> 경량 모델만 사용")
    calls.clear()
    client.post(
        "/api/v1/incidents",
        json={"channel": "C-INCIDENT", "service": "payment", "message": "PG approve request timeout after 30000ms"},
        headers=headers,
    )
    final3 = drain(c)[0]
    print("결과     :", final3["outcome"])
    print("LLM 호출 :", calls)
    print("참고 문서:", list(dict.fromkeys(d["doc_id"] for d in final3["context_docs"])))
    print("검색 통계:", c.retriever.stats, "임베딩 캐시:", c.embedder.stats)


if __name__ == "__main__":
    main()
