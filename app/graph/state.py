from typing import TypedDict


class IncidentState(TypedDict, total=False):
    incident_id: str
    incident: dict
    fingerprint: str

    classification: dict
    context_docs: list[dict]
    root_cause: dict

    fix: dict
    reused_fix: bool
    attempts: int
    history: list[dict]          # 실패한 이전 시도(패치 요약 + 잘라낸 실패 로그)

    diff_analysis: dict
    impact: dict
    test_selection: dict
    test_result: dict

    outcome: str                 # resolved | escalated | no_code_fix | budget_exceeded
    error: str
    report: str
    patch_path: str
    cost: dict
