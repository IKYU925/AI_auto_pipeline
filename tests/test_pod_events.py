import httpx
from fastapi.testclient import TestClient

from app.kube import Kube
from app.llm import prompts
from app.main import create_app
from app.pod_events import read_pod_events

POD = {"status": {"containerStatuses": [{
    "name": "shopapi", "restartCount": 0,
    "state": {"waiting": {"reason": "ImagePullBackOff", "message": 'Back-off pulling image "reg/aiops:s21-missing"'}}}]}}
EVENTS = {"items": [
    {"type": "Normal", "reason": "Scheduled", "count": 1, "lastTimestamp": "2026-11-18T01:00:00Z", "message": "assigned"},
    {"type": "Warning", "reason": "Failed", "count": 3, "lastTimestamp": "2026-11-18T01:01:00Z",
     "message": 'Failed to pull image "reg/aiops:s21-missing": not found'},
]}


def kube(pod_status=200):
    def handle(req):
        if req.url.path.endswith("/events"):
            assert req.url.params["fieldSelector"] == "involvedObject.name=shopapi-7d9-abc"
            return httpx.Response(200, json=EVENTS)
        return httpx.Response(pod_status, json=POD)
    return Kube(httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(handle)))


def test_image_pull_failure_shows_the_waiting_reason_and_newest_event_first():
    text = read_pod_events(kube(), "tenant-a", "shopapi-7d9-abc")
    lines = text.splitlines()
    assert lines[0] == '컨테이너 shopapi, 대기 ImagePullBackOff: Back-off pulling image "reg/aiops:s21-missing"'
    assert lines[1].startswith('Warning Failed x3: Failed to pull image "reg/aiops:s21-missing": not found')
    assert lines[2].startswith("Normal Scheduled x1")


def test_oom_kill_shows_the_last_termination_reason():
    class K:
        def get(self, path, **params):
            if path.endswith("/events"):
                return {"items": []}
            return {"status": {"containerStatuses": [{"name": "shopapi", "restartCount": 4, "state": {"running": {}},
                                                       "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}]}}
    assert read_pod_events(K(), "shop", "p") == "컨테이너 shopapi, 재시작 4회, 직전 종료 OOMKilled(exit 137)"


def test_a_deleted_pod_still_yields_its_events_and_nothing_yields_none():
    assert read_pod_events(kube(pod_status=404), "tenant-a", "shopapi-7d9-abc").startswith("Warning Failed x3")

    def deny(req):
        return httpx.Response(403)
    assert read_pod_events(Kube(httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(deny))), "shop", "p") is None


def test_pod_alert_intake_carries_events_into_both_prompts(container):
    container.__dict__["kube"] = kube()
    container.__dict__["loki"] = None
    alert = {"status": "firing", "fingerprint": "fp-e", "startsAt": "2026-11-18T01:01:00Z", "endsAt": "0001-01-01T00:00:00Z",
             "labels": {"alertname": "PodImagePullFailed", "namespace": "tenant-a", "pod": "shopapi-7d9-abc",
                        "container": "shopapi", "severity": "critical"},
             "annotations": {"summary": "image pull failed"}}
    client = TestClient(create_app(container), headers={"Authorization": "Bearer demo-token"})
    assert client.post("/api/v1/alerts/alertmanager", json={"alerts": [alert]}).status_code == 202
    [(_, payload)] = container.queue.consume("t", count=10, block_ms=10)
    inc = payload["incident"]
    assert "ImagePullBackOff" in inc["events"]
    assert "Failed to pull image" in prompts.classify_user(inc["service"], inc["message"], None, None, inc["events"])
    assert "Failed to pull image" in prompts.root_cause_user(inc, {"category": "config", "summary": "s"}, "", "")
    assert "(Pod 알림 아님)" in prompts.root_cause_user({"service": "s", "message": "m"}, {"category": "x", "summary": "s"}, "", "")
