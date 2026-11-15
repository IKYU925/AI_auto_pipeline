"""4단계 AWS 입구. CloudWatch 경보 상태 변경(EventBridge -> SQS)을 장애 접수로 바꾼다(plan.md 10-2).

AWS 리소스는 infra_template의 terraform/envs/aiops-lab이 만든다. 여기서는 큐를 읽고 지우고, 로그를 조회만 한다.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.schemas import IncidentIn

log = logging.getLogger(__name__)
LOOKBACK = timedelta(minutes=5)
QUERY_TIMEOUT_SEC = 30
# 로그 지표 필터 경보만 스택트레이스가 있다. 디스크 같은 지표 경보는 문구만으로 분류하고 런북을 안내한다
LOG_ALARMS = {"ErrorLogs"}
EXCEPTION_QUERY = 'fields exc_info | filter level = "ERROR" and ispresent(exc_info) | sort @timestamp desc | limit 1'


@dataclass(frozen=True)
class AlarmEvent:
    alarm_name: str
    tenant: str
    service: str
    alertname: str
    state: str
    reason: str
    at: datetime

    @classmethod
    def parse(cls, body: str) -> "AlarmEvent":
        try:
            event = json.loads(body)
            detail = event["detail"]
            name = detail["alarmName"]
            state = detail["state"]
            # 고객사 이름(tenant-a)에 하이픈이 있어서 오른쪽부터 나눈다. 서비스와 경보 종류에는 하이픈을 쓰지 않는다(결정 0013)
            tenant, service, alertname = name.rsplit("-", 2)
            at = datetime.fromisoformat(state.get("timestamp") or event["time"])
            return cls(name, tenant, service, alertname, state["value"], state.get("reason", ""), at)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise ValueError(f"CloudWatch 경보 이벤트가 아니다: {e}") from e

    @property
    def alert_fingerprint(self) -> str:
        return f"cloudwatch:{self.alarm_name}"

    def to_incident(self, channel: str, stacktrace: str | None) -> IncidentIn:
        # 지문은 첫 줄만 본다. reason은 이전 상태와 측정값에 따라 문구가 달라서 둘째 줄에 둔다
        return IncidentIn(
            source="cloudwatch", channel=channel, service=self.service, tenant=self.tenant, severity="critical",
            message=f"[{self.alertname}] CloudWatch 경보 {self.alarm_name}\n{self.reason}", stacktrace=stacktrace,
            occurred_at=self.at, alertname=self.alertname, alert_fingerprint=self.alert_fingerprint,
        )


class CloudWatchLogs:
    def __init__(self, client, log_group: str):
        self.client = client
        self.log_group = log_group

    def latest_exception(self, since: datetime, until: datetime) -> str | None:
        """조회에 실패해도 접수는 한다(Loki 조회와 같다)."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            query_id = self.client.start_query(
                logGroupName=self.log_group, queryString=EXCEPTION_QUERY,
                startTime=int((since - LOOKBACK).timestamp()), endTime=int(until.timestamp()) + 1,
            )["queryId"]
            deadline = time.monotonic() + QUERY_TIMEOUT_SEC
            while (res := self.client.get_query_results(queryId=query_id))["status"] in ("Scheduled", "Running"):
                if time.monotonic() > deadline:
                    self.client.stop_query(queryId=query_id)
                    log.warning("logs insights query timed out on %s", self.log_group)
                    return None
                time.sleep(1)
        except (BotoCoreError, ClientError) as e:
            log.warning("logs insights lookup failed on %s: %s", self.log_group, e)
            return None
        for row in res.get("results", []):
            for f in row:
                if f["field"] == "exc_info" and f["value"]:
                    return f["value"]
        return None


def handle(c, event: AlarmEvent) -> str:
    """돌려주는 값은 처리 결과(accepted, already_open, resolved, ignored). 예외가 나면 메시지를 지우지 않는다."""
    if event.state == "OK":
        return "resolved" if c.store.resolve_alert(event.alert_fingerprint, event.at) else "ignored"
    if event.state != "ALARM":
        return "ignored"
    if c.store.open_alert_incident(event.alert_fingerprint):
        return "already_open"
    stacktrace = None
    if event.alertname in LOG_ALARMS and c.cloudwatch_logs:
        stacktrace = c.cloudwatch_logs.latest_exception(event.at, datetime.now(timezone.utc))
    from app.main import accept

    accept(c, event.to_incident(c.settings.channel_for(event.tenant), stacktrace))
    return "accepted"


class SqsIntake:
    """처리를 마친 메시지만 지운다. 처리는 접수(Redis 큐 적재)까지라 몇 초 안에 끝난다. LLM 처리는 워커가 한다."""

    def __init__(self, c, client, queue_url: str):
        self.c = c
        self.client = client
        self.queue_url = queue_url
        self.stop = threading.Event()

    def poll_once(self, wait_sec: int = 20) -> list[str]:
        # 한 번에 1개씩 받는다. Logs 조회(최대 30초)가 이어져도 visibility timeout(300초) 안에 지울 수 있다
        resp = self.client.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=wait_sec)
        outcomes = []
        for msg in resp.get("Messages", []):
            try:
                event = AlarmEvent.parse(msg["Body"])
            except ValueError as e:
                # 다시 받아도 파싱할 수 없다. 남겨 두면 계속 되돌아온다
                log.warning("dropping sqs message %s: %s", msg.get("MessageId"), e)
                outcome = "malformed"
            else:
                outcome = handle(self.c, event)
                log.info("cloudwatch alarm %s %s -> %s", event.alarm_name, event.state, outcome)
            self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=msg["ReceiptHandle"])
            outcomes.append(outcome)
        return outcomes

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("sqs intake failed; message stays in the queue until visibility timeout")
                self.stop.wait(5)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="sqs-intake", daemon=True)
        t.start()
        return t
