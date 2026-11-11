"""LLM 구조화 출력 스키마. 자유 텍스트 대신 스키마로 받아야 다음 노드에서 안정적으로 쓸 수 있다."""
from typing import Literal

from pydantic import BaseModel, Field


class Classification(BaseModel):
    category: Literal["code_bug", "config", "infra", "dependency", "data", "unknown"]
    needs_code_fix: bool = Field(description="애플리케이션 코드 수정이 필요한 장애인지")
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(description="한 줄 요약")
    keywords: list[str] = Field(default_factory=list, description="RAG 검색에 쓸 핵심 키워드")


class RootCause(BaseModel):
    root_cause: str
    suspected_files: list[str] = Field(description="저장소 루트 기준 상대 경로")
    evidence: list[str] = Field(default_factory=list)
    fix_strategy: str


class FixProposal(BaseModel):
    patch: str = Field(description="저장소 루트 기준 unified diff (--- a/path, +++ b/path 형식)")
    explanation: str
    risk: Literal["low", "medium", "high"] = "medium"
