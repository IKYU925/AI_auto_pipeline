import json

import httpx
from fastapi.testclient import TestClient

from app.loki import LokiClient
from app.main import create_app

BEARER = {"Authorization": "Bearer demo-token"}
TRACE = 'Traceback (most recent call last):\n  File "/repo/shopapi/orders.py", line 42, in average_unit_price\nZeroDivisionError: float division by zero'


def alert(name, status="firing", fp="fp-1", **labels):
    return {
        "status": status,
        "labels": {"alertname": name, "namespace": "shop", "severity": "critical", **labels},
        "annotations": {"summary": f"{name} on shop"},
        "startsAt": "2026-11-14T10:00:00Z",
        "endsAt": "2026-11-14T10:20:00Z" if status == "resolved" else "0001-01-01T00:00:00Z",
        "generatorURL": "http://prometheus/graph",
        "fingerprint": fp,
    }


def webhook(*alerts):
    return {"version": "4", "groupKey": "{}:{}", "status": "firing", "receiver": "ai-automation",
            "groupLabels": {}, "commonLabels": {}, "commonAnnotations": {}, "externalURL": "http://am", "alerts": list(alerts)}


def fake_loki(lines=None, fail=False):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        if fail:
            return httpx.Response(503)
        values = [[str(1794650000000000000 + i), json.dumps(l)] for i, l in enumerate(lines or [])]
        return httpx.Response(200, json={"data": {"result": [{"stream": {}, "values": values}]}})

    return LokiClient("http://loki", transport=httpx.MockTransport(handle)), seen


def test_firing_alerts_become_incidents_and_resolved_marks_the_incident(container):
    loki, queries = fake_loki([{"level": "ERROR", "message": "older", "exc_info": "old trace"},
                               {"level": "ERROR", "message": "unhandled", "exc_info": TRACE}])
    container.__dict__["loki"] = loki
    client = TestClient(create_app(container), headers=BEARER)

    res = client.post("/api/v1/alerts/alertmanager", json=webhook(
        alert("ShopapiException", fp="fp-exc", service="shopapi"),
        alert("KubePodCrashLooping", fp="fp-crash", pod="shopapi-7d9", container="shopapi", service="kube-state-metrics"),
    )).json()
    assert len(res["accepted"]) == 2 and res["resolved"] == 0

    exc = client.get(f"/api/v1/incidents/{res['accepted'][0]}").json()
    crash_id = res["accepted"][1]
    payloads = {p["incident_id"]: p["incident"] for _, p in container.queue.consume("t", count=10, block_ms=10)}
    assert payloads[res["accepted"][0]]["stacktrace"] == TRACE, "가장 최근 ERROR 로그의 exc_info"
    assert payloads[crash_id]["service"] == "shopapi", "kube-state-metrics 라벨 대신 container"
    assert payloads[crash_id]["stacktrace"] is None and len(queries) == 1, "코드 버그 계열만 Loki 조회"
    assert queries[0] == '{namespace="shop", service="shopapi"} | json | level="ERROR"'
    assert exc["status"] == "queued"

    res = client.post("/api/v1/alerts/alertmanager", json=webhook(alert("KubePodCrashLooping", "resolved", fp="fp-crash")))
    assert res.json()["resolved"] == 1
    with container.db.connection() as conn:
        row = conn.execute("SELECT alertname, alert_resolved_at FROM incidents WHERE id = %s", (crash_id,)).fetchone()
    assert row["alertname"] == "KubePodCrashLooping" and row["alert_resolved_at"].isoformat().startswith("2026-11-14T10:20")


def test_repeated_firing_alert_does_not_open_a_second_incident(container):
    client = TestClient(create_app(container), headers=BEARER)
    first = client.post("/api/v1/alerts/alertmanager", json=webhook(alert("ShopapiSlowPayment", fp="fp-slow", service="shopapi"))).json()
    # Alertmanager의 repeat_interval(1h) 뒤 재전송: 지문 중복 제거 TTL은 지났다고 본다
    for key in container.redis.scan_iter("dedupe:*"):
        container.redis.delete(key)
    again = client.post("/api/v1/alerts/alertmanager", json=webhook(alert("ShopapiSlowPayment", fp="fp-slow", service="shopapi"))).json()
    assert again["accepted"] == [] and again["already_open"] == first["accepted"]


def test_loki_failure_still_accepts_without_stacktrace(container):
    container.__dict__["loki"], _ = fake_loki(fail=True)
    client = TestClient(create_app(container), headers=BEARER)
    res = client.post("/api/v1/alerts/alertmanager", json=webhook(alert("ShopapiException", fp="fp-x", service="shopapi"))).json()
    [(_, payload)] = list(container.queue.consume("t", count=1, block_ms=10))
    assert res["accepted"] and payload["incident"]["stacktrace"] is None


def test_both_token_headers_work_and_wrong_token_is_rejected(container):
    client = TestClient(create_app(container))
    body = webhook()
    assert client.post("/api/v1/alerts/alertmanager", json=body, headers=BEARER).status_code == 202
    assert client.post("/api/v1/alerts/alertmanager", json=body, headers={"X-Automation-Token": "demo-token"}).status_code == 202
    assert client.post("/api/v1/alerts/alertmanager", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
