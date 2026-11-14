import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.worker import process_message
from tests.test_pipeline import HEADERS, INCIDENT
from tests.test_slack_card import RecordingClient


class WorkerKilled(BaseException):
    """kill -9처럼 except Exception으로 잡히지 않는 중단."""


@pytest.fixture
def recorder(container):
    from app.notify.slack import SlackNotifier

    notifier = SlackNotifier("")
    notifier.client = RecordingClient()
    container.__dict__["notifier"] = notifier
    return notifier.client


def _kill_first_test_run(monkeypatch):
    from app.testing.runner import SandboxTestRunner

    original = SandboxTestRunner.run
    calls = {"n": 0}

    def run(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise WorkerKilled("run_tests")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SandboxTestRunner, "run", run)


def test_killed_worker_resumes_from_checkpoint_without_repeating_llm_calls(container, llm_calls, recorder, monkeypatch):
    _kill_first_test_run(monkeypatch)
    client = TestClient(create_app(container))
    inc_id = client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()["incident_id"]

    [(msg_id, payload)] = list(container.queue.consume("w1", count=1, block_ms=10))
    with pytest.raises(WorkerKilled):
        process_message(container, msg_id, payload)
    before = list(llm_calls)
    assert container.redis.xpending(container.settings.queue_stream, container.settings.queue_group)["pending"] == 1

    [(msg_id2, payload2)] = container.queue.reclaim_stale("w2", min_idle_ms=0)
    final = process_message(container, msg_id2, payload2)

    assert final["outcome"] == "resolved"
    assert llm_calls[: len(before)] == before and len(llm_calls) - len(before) == 1, "분류, 원인 분석, 첫 수정안은 다시 부르지 않는다"
    status = client.get(f"/api/v1/incidents/{inc_id}", headers=HEADERS).json()
    assert status["resumed_from"] == ["run_tests"]
    assert len(recorder.sent) == 2, "카드와 패치 한 번씩"


def test_redelivered_message_after_reply_is_not_replied_twice(container, recorder):
    client = TestClient(create_app(container))
    client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)
    [(msg_id, payload)] = list(container.queue.consume("w1", count=1, block_ms=10))
    process_message(container, msg_id, payload)
    sent = len(recorder.sent)

    final = process_message(container, msg_id, payload)
    assert final["outcome"] == "resolved" and len(recorder.sent) == sent


def test_without_checkpoint_the_reclaimed_message_starts_over(tmp_path, llm_calls, database, monkeypatch):
    from app.container import Container
    from app.db import truncate_all
    from app.llm.fake import fake_model_factory
    from scripts.demo_offline import offline_settings
    from scripts.demo_responder import demo_responder

    c = Container(offline_settings(artifacts_dir=str(tmp_path / "a"), checkpoint_enabled=False),
                  model_factory=fake_model_factory(demo_responder, llm_calls))
    truncate_all(c.db)
    try:
        _kill_first_test_run(monkeypatch)
        TestClient(create_app(c)).post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)
        [(msg_id, payload)] = list(c.queue.consume("w1", count=1, block_ms=10))
        with pytest.raises(WorkerKilled):
            process_message(c, msg_id, payload)
        before = len(llm_calls)

        [(msg_id2, payload2)] = c.queue.reclaim_stale("w2", min_idle_ms=0)
        assert process_message(c, msg_id2, payload2)["outcome"] == "resolved"
        heavy_again = [m for m, _ in llm_calls[before:] if m == "fake:heavy"]
        assert len(heavy_again) == 2, "원인 분석은 응답 캐시에 맞고, 캐시되지 않는 수정안 생성을 처음부터 다시 부른다"
    finally:
        c.db.close()
