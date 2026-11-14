from fastapi.testclient import TestClient

from app.main import create_app
from app.notify.slack import SlackNotifier
from app.worker import process_message


class FailingClient:
    def chat_postMessage(self, **kwargs):
        from slack_sdk.errors import SlackApiError

        raise SlackApiError("channel_not_found", {"ok": False, "error": "channel_not_found"})


def test_slack_reply_failure_does_not_lose_the_incident(container):
    notifier = SlackNotifier("")
    notifier.client = FailingClient()
    container.__dict__["notifier"] = notifier
    client = TestClient(create_app(container), headers={"X-Automation-Token": "demo-token"})
    inc = client.post("/api/v1/incidents", json={"channel": "C-NOPE", "service": "payment", "message": "PG timeout"}).json()

    [(msg_id, payload)] = list(container.queue.consume("t", count=1, block_ms=10))
    final = process_message(container, msg_id, payload)

    assert final["outcome"] == "no_code_fix"
    assert client.get(f"/api/v1/incidents/{inc['incident_id']}").json()["status"] == "no_code_fix"
    assert container.redis.xpending(container.settings.queue_stream, container.settings.queue_group)["pending"] == 0, "처리한 메시지는 ack"
