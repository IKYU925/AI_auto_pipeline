from fastapi.testclient import TestClient

from app.main import create_app
from app.rag.loader import load_markdown_dir
from app.worker import process_message
from scripts.demo_offline import STACK

HEADERS = {"X-Automation-Token": "demo-token"}
INCIDENT = {
    "channel": "C1", "thread_ts": "1.0", "service": "checkout",
    "message": "500 on /orders/summary order_id=1: ZeroDivisionError", "stacktrace": STACK,
}


def _drain(c):
    return [process_message(c, mid, p) for mid, p in c.queue.consume("t", count=10, block_ms=10)]


def test_api_rejects_bad_token(container):
    client = TestClient(create_app(container))
    assert client.post("/api/v1/incidents", json=INCIDENT).status_code == 401


def test_api_dedupes_same_error(container):
    client = TestClient(create_app(container))
    a = client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()
    b = client.post("/api/v1/incidents", json={**INCIDENT, "message": INCIDENT["message"].replace("=1", "=2")},
                    headers=HEADERS).json()
    assert b["deduplicated"] and a["incident_id"] == b["incident_id"]
    assert container.redis.xlen(container.settings.queue_stream) == 1


def test_regression_loop_retries_until_tests_pass(container, llm_calls):
    container.ingestor.ingest(load_markdown_dir("knowledge"))
    client = TestClient(create_app(container))
    inc_id = client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()["incident_id"]

    final = _drain(container)[0]
    assert final["outcome"] == "resolved"
    assert final["attempts"] == 2
    assert "2 failed" in final["history"][0]["failure"]          # 첫 수정안은 회귀 테스트에서 실패
    assert final["test_selection"]["tests"] == ["tests/test_cart.py", "tests/test_pricing.py"]
    assert [c[0] for c in llm_calls] == ["fake:light", "fake:heavy", "fake:heavy", "fake:heavy"]
    assert {d["doc_id"] for d in final["context_docs"]} <= {"runbooks/checkout-unit-price.md", "common/deploy-rollback.md"}

    status = client.get(f"/api/v1/incidents/{inc_id}", headers=HEADERS).json()
    assert status["status"] == "resolved" and status["patch_path"].endswith(".patch")


def test_recurrence_reuses_verified_fix_without_llm(container, llm_calls):
    client = TestClient(create_app(container))
    first = client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()
    _drain(container)
    container.redis.delete(f"dedupe:{first['fingerprint']}")
    llm_calls.clear()

    client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)
    final = _drain(container)[0]
    assert final["outcome"] == "resolved" and final["reused_fix"]
    assert llm_calls == []


def test_infra_incident_uses_light_model_only(container, llm_calls):
    client = TestClient(create_app(container))
    client.post("/api/v1/incidents", json={"channel": "C1", "service": "payment", "message": "PG timeout"}, headers=HEADERS)
    final = _drain(container)[0]
    assert final["outcome"] == "no_code_fix"
    assert llm_calls == [("fake:light", "Classification")]


def test_escalates_after_max_attempts(container):
    container.settings.max_fix_attempts = 1
    container.__dict__.pop("graph", None)
    client = TestClient(create_app(container))
    client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)
    final = _drain(container)[0]
    assert final["outcome"] == "escalated" and final["attempts"] == 1
