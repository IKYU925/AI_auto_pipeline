"""워커의 최종 상태로 Slack 회신 카드(Block Kit)를 만든다. 보내는 일은 SlackNotifier가 한다."""
from dataclasses import dataclass

# Block Kit 한도: 섹션 텍스트 3,000자, 헤더 150자
SECTION_MAX = 2900
HEADER_MAX = 150


@dataclass(frozen=True)
class Look:
    emoji: str
    title: str


LOOKS = {
    "resolved": Look("✅", "수정안 검증 완료 · 승인 대기"),
    "no_code_fix": Look("📘", "코드 수정 대상 아님 · 런북 안내"),
    "escalated": Look("⚠️", "자동 수정 실패 · 담당자 확인"),
    "budget_exceeded": Look("💸", "LLM 비용 상한 도달 · 담당자 확인"),
}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _section(label: str, body: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _clip(f"*{label}*\n{body}", SECTION_MAX)}}


def _fields(state: dict) -> dict:
    cls = state.get("classification") or {}
    cost = state.get("cost") or {}
    attempts = "수정안 재사용(LLM 호출 없음)" if state.get("reused_fix") else f"{state.get('attempts', 0)}회"
    category = f"{cls['category']} ({cls.get('confidence', 0):.2f})" if cls else "-"
    pairs = [("서비스", f"`{state['incident']['service']}`"), ("분류", category), ("시도", attempts), ("LLM 비용", f"${cost.get('usd', 0):.4f}")]
    return {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in pairs]}


def _tests(state: dict) -> str:
    result = state.get("test_result") or {}
    selection = state.get("test_selection") or {}
    return " · ".join(p for p in (result.get("summary"), selection.get("reason")) if p) or "-"


def build_card(state: dict) -> tuple[str, list[dict]]:
    """알림 미리보기용 text와 카드 blocks를 돌려준다."""
    outcome = state["outcome"]
    look = LOOKS.get(outcome, Look("❓", outcome))
    service = state["incident"]["service"]
    headline = state["incident"]["message"].splitlines()[0]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": _clip(f"{look.emoji} {look.title}", HEADER_MAX)}},
        _fields(state),
        _section("장애", f"`{_clip(headline, 300)}`"),
    ]
    root_cause = (state.get("root_cause") or {}).get("root_cause")
    if outcome == "resolved":
        fix = state.get("fix") or {}
        if root_cause:
            blocks.append(_section("원인", _clip(root_cause, 600)))
        blocks.append(_section("수정", f"{_clip(fix.get('explanation', '-'), 600)} (위험도 {fix.get('risk', '-')})"))
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _clip(f"테스트: {_tests(state)}", 2000)}]})
    elif outcome == "no_code_fix":
        summary = (state.get("classification") or {}).get("summary", "-")
        docs = ", ".join(dict.fromkeys(d["doc_id"] for d in state.get("context_docs", []))) or "-"
        blocks.append(_section("판단", _clip(summary, 600)))
        blocks.append(_section("참고 런북", docs))
    elif outcome == "escalated":
        if root_cause:
            blocks.append(_section("원인", _clip(root_cause, 600)))
        blocks.append(_section("마지막 테스트", _tests(state)))
    elif state.get("error"):
        blocks.append(_section("중단 사유", _clip(state["error"], 600)))
    return f"{look.emoji} {look.title} · {service} · {_clip(headline, 120)}", blocks


def patch_message(state: dict) -> str | None:
    """검증을 통과한 패치는 카드가 길어지지 않게 같은 스레드의 다음 메시지로 보낸다."""
    patch = (state.get("fix") or {}).get("patch")
    if state.get("outcome") != "resolved" or not patch:
        return None
    return f"*패치*\n```\n{_clip(patch, 2800)}\n```"
