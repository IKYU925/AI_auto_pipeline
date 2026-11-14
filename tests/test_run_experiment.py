import shutil

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.llm.fake import fake_model_factory
from app.llm.schemas import Classification, FixProposal, RootCause
from app.main import create_app
from app.worker import process_message
from scripts import run_experiment as rx
from scripts.demo_offline import offline_settings

GOOD_FIX = """--- a/shopapi/orders.py
+++ b/shopapi/orders.py
@@ -42,4 +42,6 @@
     quantity = sum(i.quantity for i in items)
+    if quantity == 0:
+        return 0.0
     return round(subtotal(items) / quantity, 2)
"""

# 선별 테스트는 통과하지만 스펙(빈 주문 평균 단가 0.0)과 다르다
WRONG_FIX = GOOD_FIX.replace("return 0.0", "return None")


def responder(fix: str):
    def respond(schema, messages):
        text = str(messages[-1].content)
        if schema is Classification:
            if "ShopapiSlowPayment" in text:
                return Classification(category="infra", needs_code_fix=False, confidence=0.9, summary="PG 지연")
            if "MissingSetting" in text:
                return Classification(category="config", needs_code_fix=False, confidence=0.9, summary="환경변수 누락")
            return Classification(category="code_bug", needs_code_fix=True, confidence=0.9, summary="코드 버그")
        if schema is RootCause:
            return RootCause(root_cause="수량 0 가드 누락", suspected_files=["shopapi/orders.py"], fix_strategy="가드 복구")
        if schema is FixProposal:
            return FixProposal(patch=fix, explanation="수량 0 가드", risk="low")
        raise ValueError(schema)

    return respond


@pytest.fixture
def shopapi(tmp_path, monkeypatch):
    repo = tmp_path / "shopapi"
    shutil.copytree(rx.SHOPAPI, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    monkeypatch.setattr(rx, "SHOPAPI", repo)
    return repo


def backend(shopapi, tmp_path, fix=GOOD_FIX) -> rx.Backend:
    c = Container(
        offline_settings(target_repo_path=str(shopapi), artifacts_dir=str(tmp_path / "artifacts")),
        model_factory=fake_model_factory(responder(fix), []),
    )

    def drain():
        for mid, payload in c.queue.consume("t", count=20, block_ms=10):
            process_message(c, mid, payload)

    api = TestClient(create_app(c), headers={"X-Automation-Token": "demo-token"})
    return rx.Backend(api, c.redis, process=drain, interval_sec=0)


def run(sid, shopapi, tmp_path, fix=GOOD_FIX) -> dict:
    return rx.run_scenario(rx.load_scenario(sid), backend(shopapi, tmp_path, fix))


def test_code_bug_good_fix_passes_hidden_and_full_suite(shopapi, tmp_path):
    original = (shopapi / "shopapi" / "orders.py").read_text()
    row = run("S01", shopapi, tmp_path)
    expected = {"outcome": "resolved", "outcome_correct": True, "category_correct": True, "attempts": 1,
                "hidden_passed": True, "full_passed": True, "false_pass": False, "llm_calls": 3, "reused_fix": False}
    assert {k: row[k] for k in expected} == expected
    assert 0 < row["tests_selected"] < row["test_files_total"], "영향 범위 기반 선별"
    assert (shopapi / "shopapi" / "orders.py").read_text() == original, "버그 원복"


def test_code_bug_wrong_fix_is_counted_as_false_pass(shopapi, tmp_path):
    row = run("S01", shopapi, tmp_path, fix=WRONG_FIX)
    assert (row["outcome"], row["hidden_passed"], row["false_pass"]) == ("resolved", False, True)


def test_alert_scenario_ends_with_runbook(shopapi, tmp_path):
    row = run("S10", shopapi, tmp_path)
    assert (row["outcome"], row["category_correct"], row["llm_calls"]) == ("no_code_fix", True, 1)


def test_startup_failure_scenario_is_config_incident(shopapi, tmp_path):
    row = run("S11", shopapi, tmp_path)
    assert (row["outcome"], row["category"]) == ("no_code_fix", "config")


def test_recurrence_reuses_fix_without_llm(shopapi, tmp_path):
    row = run("S12", shopapi, tmp_path)
    assert (row["outcome"], row["llm_calls"], row["reused_fix"], row["hidden_passed"]) == ("resolved", 0, True, True)
    assert row["usd"] == 0 and row["procedure_usd"] > 0, "base 처리 비용은 절차 비용에 들어간다"


def test_duplicate_alerts_create_one_incident(shopapi, tmp_path):
    row = run("S13", shopapi, tmp_path)
    assert row["incidents_created"] == 1


def test_each_run_starts_with_empty_caches(shopapi, tmp_path):
    b = backend(shopapi, tmp_path)
    s = rx.load_scenario("S01")
    rx.run_scenario(s, b)
    assert rx.run_scenario(s, b)["llm_calls"] == 3


def test_condition_mismatch_stops_before_running(shopapi, tmp_path):
    b = backend(shopapi, tmp_path)
    assert rx.verify_condition(b.api, "A")["optimizations"]["llm_cache"] is True
    with pytest.raises(SystemExit, match="조건 B"):
        rx.verify_condition(b.api, "B")


def test_csv_has_header_once(tmp_path):
    out = tmp_path / "r.csv"
    rx.append_csv(out, {"scenario": "S01", "run": 1})
    rx.append_csv(out, {"scenario": "S02", "run": 1})
    lines = out.read_text().splitlines()
    assert lines[0].split(",") == rx.COLUMNS and len(lines) == 3


def test_run_all_stops_at_cost_ceiling(shopapi, tmp_path):
    b = backend(shopapi, tmp_path)
    out = tmp_path / "runs.csv"
    one = rx.run_all([rx.load_scenario("S01")], b, "A", 1, None)
    assert one > 0
    spent = rx.run_all([rx.load_scenario("S01")], b, "A", 5, out, max_usd=one * 1.5)
    rows = out.read_text().splitlines()[1:]
    assert len(rows) == 2, "두 번째 실행 뒤 상한을 넘어 멈춤"
    assert spent >= one * 1.5


@pytest.mark.parametrize("scenario_id", ["S01", "S02"])
def test_inject_changes_files_inside_a_git_repo(tmp_path, monkeypatch, scenario_id):
    """S01은 일반 diff, S02는 git diff 형식이다. 저장소 하위 폴더에서도 둘 다 실제로 적용돼야 한다."""
    import subprocess

    repo = tmp_path / "targets" / "shopapi"
    shutil.copytree(rx.SHOPAPI, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    before = {p: p.read_text() for p in repo.rglob("*.py")}
    with rx.injected(scenario_id, repo):
        changed = [p for p, text in before.items() if p.read_text() != text]
        assert changed, "패치가 실제로 파일을 바꿔야 한다"
    assert all(p.read_text() == text for p, text in before.items()), "원복"
