from fastapi.testclient import TestClient

from app.main import create_app
from app.notify.card import SECTION_MAX, build_card, patch_message
from app.notify.slack import SlackNotifier
from app.worker import process_message
from tests.test_pipeline import HEADERS, INCIDENT


class RecordingClient:
    def __init__(self):
        self.sent = []

    def chat_postMessage(self, **kwargs):
        self.sent.append(kwargs)


def _texts(blocks):
    out = []
    for b in blocks:
        if "text" in b:
            out.append(b["text"]["text"])
        out += [f["text"] for f in b.get("fields", [])]
        out += [e["text"] for e in b.get("elements", [])]
    return "\n".join(out)


def _state(outcome, **extra):
    return {
        "incident": {"service": "checkout", "message": "PG timeout on /pay\nmore lines"},
        "classification": {"category": "infra", "confidence": 0.9, "summary": "결제 게이트웨이 지연"},
        "attempts": 0, "cost": {"usd": 0.0123}, "outcome": outcome, **extra,
    }


def test_resolved_card_then_patch_in_same_thread(container):
    recorder = RecordingClient()
    notifier = SlackNotifier("")
    notifier.client = recorder
    container.__dict__["notifier"] = notifier
    client = TestClient(create_app(container))
    client.post("/api/v1/incidents", json=INCIDENT, headers=HEADERS)

    [(msg_id, payload)] = list(container.queue.consume("t", count=1, block_ms=10))
    final = process_message(container, msg_id, payload)

    card, patch = recorder.sent
    assert final["outcome"] == "resolved"
    assert card["text"].startswith("✅ 수정안 검증 완료") and "checkout" in card["text"]
    body = _texts(card["blocks"])
    for label in ("서비스", "분류", "시도", "LLM 비용", "원인", "수정", "테스트"):
        assert label in body
    assert "```" not in body, "패치는 카드에 넣지 않는다"
    assert "```" in patch["text"] and patch.get("blocks") is None
    assert card["thread_ts"] == patch["thread_ts"] == INCIDENT["thread_ts"]


def test_no_code_fix_card_names_runbooks_and_sends_no_patch():
    state = _state("no_code_fix", context_docs=[{"doc_id": "runbooks/pg.md"}, {"doc_id": "runbooks/pg.md"}])
    text, blocks = build_card(state)
    assert text == "📘 코드 수정 대상 아님 · 런북 안내 · checkout · PG timeout on /pay"
    assert "runbooks/pg.md" in _texts(blocks) and _texts(blocks).count("runbooks/pg.md") == 1
    assert patch_message(state) is None


def test_escalated_and_budget_cards():
    text, _ = build_card(_state("escalated", test_result={"summary": "2 failed"}))
    assert text.startswith("⚠️ 자동 수정 실패")
    text, blocks = build_card(_state("budget_exceeded", error="budget $0.50 exceeded"))
    assert text.startswith("💸") and "budget $0.50 exceeded" in _texts(blocks)


def test_long_fields_stay_inside_block_kit_limits():
    huge = "x" * 10_000
    state = _state("resolved", root_cause={"root_cause": huge}, fix={"explanation": huge, "risk": "low", "patch": huge},
                   incident={"service": "checkout", "message": huge})
    text, blocks = build_card(state)
    assert len(text) < 300
    assert all(len(b["text"]["text"]) <= SECTION_MAX for b in blocks if b["type"] == "section" and "text" in b)
    assert len(patch_message(state)) < 3000


def test_reused_fix_card_says_no_llm_call():
    _, blocks = build_card(_state("resolved", reused_fix=True, fix={"explanation": "가드 추가", "risk": "low", "patch": "p"}))
    assert "수정안 재사용(LLM 호출 없음)" in _texts(blocks) and "0회" not in _texts(blocks)
