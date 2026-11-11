"""API 입출력 스키마."""
from datetime import datetime

from pydantic import BaseModel, Field


class IncidentIn(BaseModel):
    """n8n이 Slack 이벤트를 가공해서 보내는 장애 정보."""

    source: str = "slack"
    channel: str
    thread_ts: str | None = None
    service: str = Field(description="장애가 발생한 서비스 이름. RAG 메타데이터 필터에 쓰인다")
    severity: str = "unknown"
    message: str = Field(description="알림 본문 또는 에러 메시지")
    stacktrace: str | None = None
    occurred_at: datetime | None = None


class IncidentAccepted(BaseModel):
    incident_id: str
    status: str
    deduplicated: bool = False
    fingerprint: str


class KnowledgeDocIn(BaseModel):
    doc_id: str
    text: str
    service: str = "common"
    category: str = "general"
    doc_type: str = "runbook"          # runbook | postmortem | code_doc | adr ...
    updated_at: datetime | None = None
    source_uri: str | None = None


class KnowledgeIngestIn(BaseModel):
    documents: list[KnowledgeDocIn]
