"""API 입출력 스키마."""
from datetime import datetime

from pydantic import BaseModel, Field


class IncidentIn(BaseModel):
    """n8n이 Slack 이벤트를 가공해서 보내는 장애 정보."""

    source: str = "slack"
    channel: str
    thread_ts: str | None = None
    service: str = Field(description="장애가 발생한 서비스 이름. RAG 메타데이터 필터에 쓰인다")
    tenant: str = Field("common", description="고객사. 회신 채널, 지식 검색 범위, 지문을 고객사별로 나눈다")
    severity: str = "unknown"
    message: str = Field(description="알림 본문 또는 에러 메시지")
    stacktrace: str | None = None
    logs: str | None = Field(None, description="Pod 알림(CrashLoop, OOMKilled)의 대상 Pod 마지막 로그")
    occurred_at: datetime | None = None
    alertname: str | None = None          # Alertmanager로 들어온 장애만
    alert_fingerprint: str | None = None  # 해소(resolved) 알림을 같은 장애에 잇는 키
    deploy: dict | None = Field(None, description="알림 시점의 배포 상태(app/deploys.py). 롤백 제안의 근거")
    events: str | None = Field(None, description="Pod 알림 시점의 컨테이너 상태와 K8s 이벤트(app/pod_events.py)")


class IncidentAccepted(BaseModel):
    incident_id: str
    status: str
    deduplicated: bool = False
    fingerprint: str


class KnowledgeDocIn(BaseModel):
    doc_id: str
    text: str
    service: str = "common"
    tenant: str = "common"
    category: str = "general"
    doc_type: str = "runbook"          # runbook | postmortem | code_doc | adr ...
    updated_at: datetime | None = None
    source_uri: str | None = None


class KnowledgeIngestIn(BaseModel):
    documents: list[KnowledgeDocIn]
