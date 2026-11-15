import json
from pathlib import Path

import pytest

from app.aws import AlarmEvent, CloudWatchLogs, SqsIntake

FIXTURES = Path(__file__).parent / "fixtures"
TRACE = 'Traceback (most recent call last):\n  File "/repo/shopapi/orders.py", line 42, in average_unit_price\nZeroDivisionError: float division by zero'


def event(name: str, state: str | None = None) -> str:
    raw = json.loads((FIXTURES / name).read_text())
    if state:
        raw["detail"]["state"]["value"] = state
    return json.dumps(raw)


class FakeSqs:
    def __init__(self, *bodies: str):
        self.messages = [{"MessageId": f"m{i}", "ReceiptHandle": f"r{i}", "Body": b} for i, b in enumerate(bodies)]
        self.deleted: list[str] = []

    def receive_message(self, QueueUrl, MaxNumberOfMessages, WaitTimeSeconds):
        visible = [m for m in self.messages if m["ReceiptHandle"] not in self.deleted]
        return {"Messages": visible[:MaxNumberOfMessages]}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)


class FakeLogs:
    def __init__(self, exc_info: str | None):
        self.exc_info = exc_info
        self.queries: list[dict] = []

    def start_query(self, **kw):
        self.queries.append(kw)
        return {"queryId": "q1"}

    def get_query_results(self, queryId):
        rows = [[{"field": "exc_info", "value": self.exc_info}]] if self.exc_info else []
        return {"status": "Complete", "results": rows}


@pytest.fixture
def aws(container):
    container.settings.tenant_slack_channels = {"tenant-a": "C-TENANT-A"}
    logs = FakeLogs(TRACE)
    container.__dict__["cloudwatch_logs"] = CloudWatchLogs(logs, "/ai-incident/shopapi")
    return container, logs


def intake(container, *bodies):
    sqs = FakeSqs(*bodies)
    return SqsIntake(container, sqs, "https://sqs.example/q"), sqs


def incidents(container):
    with container.db.connection() as conn:
        return conn.execute("SELECT id, tenant, service, alertname, occurred_at, alert_resolved_at FROM incidents").fetchall()


def test_alarm_name_splits_from_the_right_so_hyphenated_tenants_survive():
    e = AlarmEvent.parse(event("cloudwatch_alarm_error_logs.json"))
    assert (e.tenant, e.service, e.alertname, e.state) == ("tenant-a", "shopapi", "ErrorLogs", "ALARM")
    assert e.at.isoformat() == "2026-11-15T10:01:30.123000+00:00"


def test_alarm_becomes_a_tenant_incident_with_the_stacktrace_and_the_message_is_deleted(aws):
    c, logs = aws
    sqs_intake, sqs = intake(c, event("cloudwatch_alarm_error_logs.json"))

    assert sqs_intake.poll_once(wait_sec=0) == ["accepted"]
    assert sqs.deleted == ["r0"]
    [(_, payload)] = c.queue.consume("t", count=10, block_ms=10)
    inc = payload["incident"]
    assert (inc["source"], inc["tenant"], inc["service"], inc["channel"]) == ("cloudwatch", "tenant-a", "shopapi", "C-TENANT-A")
    assert inc["stacktrace"] == TRACE
    assert logs.queries[0]["logGroupName"] == "/ai-incident/shopapi"
    assert logs.queries[0]["startTime"] == int(AlarmEvent.parse(event("cloudwatch_alarm_error_logs.json")).at.timestamp()) - 300, \
        "경보 시각 5분 전부터 조회"

    again, _ = intake(c, event("cloudwatch_alarm_error_logs.json"))
    assert again.poll_once(wait_sec=0) == ["already_open"], "해소 전 같은 경보는 새 장애가 아니다"
    assert len(incidents(c)) == 1


def test_ok_resolves_the_open_incident(aws):
    c, _ = aws
    sqs_intake, sqs = intake(c, event("cloudwatch_alarm_error_logs.json"), event("cloudwatch_alarm_error_logs_ok.json"))

    assert sqs_intake.poll_once(wait_sec=0) + sqs_intake.poll_once(wait_sec=0) == ["accepted", "resolved"]
    [row] = incidents(c)
    assert row["alertname"] == "ErrorLogs" and row["tenant"] == "tenant-a"
    assert row["occurred_at"].isoformat().startswith("2026-11-15T10:01:30")
    assert row["alert_resolved_at"].isoformat().startswith("2026-11-15T10:20:30")
    assert sqs.deleted == ["r0", "r1"]


def test_ok_and_insufficient_data_for_alarms_never_accepted_are_dropped(aws):
    c, _ = aws
    sqs_intake, sqs = intake(c, event("cloudwatch_alarm_disk_insufficient.json"),
                             event("cloudwatch_alarm_disk_insufficient.json", state="OK"), "not json")

    assert [sqs_intake.poll_once(wait_sec=0) for _ in range(3)] == [["ignored"], ["ignored"], ["malformed"]]
    assert sqs.deleted == ["r0", "r1", "r2"]
    assert incidents(c) == []


def test_metric_alarm_is_accepted_without_a_log_lookup(aws):
    c, logs = aws
    sqs_intake, _ = intake(c, event("cloudwatch_alarm_disk_insufficient.json", state="ALARM"))

    assert sqs_intake.poll_once(wait_sec=0) == ["accepted"]
    [(_, payload)] = c.queue.consume("t", count=10, block_ms=10)
    assert payload["incident"]["stacktrace"] is None and payload["incident"]["alertname"] == "DiskUsageHigh"
    assert logs.queries == []


def test_message_stays_in_the_queue_when_intake_fails(aws, monkeypatch):
    c, _ = aws
    sqs_intake, sqs = intake(c, event("cloudwatch_alarm_error_logs.json"))
    monkeypatch.setattr(c.store, "open_alert_incident", lambda fp: (_ for _ in ()).throw(ConnectionError("db down")))

    with pytest.raises(ConnectionError):
        sqs_intake.poll_once(wait_sec=0)
    assert sqs.deleted == [], "visibility timeout이 지나면 다시 받는다"
