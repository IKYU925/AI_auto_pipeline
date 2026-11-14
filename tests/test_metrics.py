from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.main import create_app

HEADERS = {"X-Automation-Token": "demo-token"}


def incident(message: str, source: str = "slack") -> dict:
    return {"source": source, "channel": "C1", "service": "shopapi", "message": message}


def scrape(client: TestClient) -> dict:
    res = client.get("/metrics")
    assert res.status_code == 200, "Prometheus는 토큰 없이 수집한다"
    return {(s.name, tuple(sorted(s.labels.items()))): s.value
            for family in text_string_to_metric_families(res.text) for s in family.samples}


def test_received_counter_splits_source_and_duplicates(container):
    client = TestClient(create_app(container), headers=HEADERS)
    client.post("/api/v1/incidents", json=incident("boom"))
    client.post("/api/v1/incidents", json=incident("boom"))
    client.post("/api/v1/incidents", json=incident("other", source="experiment"))

    m = scrape(TestClient(create_app(container)))
    key = "aiops_incidents_received_total"
    assert m[(key, (("deduplicated", "false"), ("source", "slack")))] == 1
    assert m[(key, (("deduplicated", "true"), ("source", "slack")))] == 1
    assert m[(key, (("deduplicated", "false"), ("source", "experiment")))] == 1


def test_queue_gauges_follow_waiting_in_progress_and_dead_letters(container):
    client = TestClient(create_app(container), headers=HEADERS)
    for message in ("timeout", "zero division", "key error"):
        client.post("/api/v1/incidents", json=incident(message))

    def gauges() -> tuple:
        m = scrape(client)
        return m[("aiops_queue_waiting", ())], m[("aiops_queue_in_progress", ())], m[("aiops_queue_dead_letters", ())]

    assert gauges() == (3, 0, 0)
    [(msg_id, payload)] = list(container.queue.consume("w1", count=1, block_ms=10))
    assert gauges() == (2, 1, 0), "가져갔지만 ack 전이면 처리 중"
    container.queue.dead_letter(msg_id, payload, "boom")
    assert gauges() == (2, 0, 1)
