import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.llm.fake import fake_model_factory
from app.llm.schemas import Classification, FixProposal, RootCause
from app.main import create_app
from app.worker import process_message
from scripts import run_experiment
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
WRONG_FIX = """--- a/shopapi/orders.py
+++ b/shopapi/orders.py
@@ -42,4 +42,6 @@
     quantity = sum(i.quantity for i in items)
+    if quantity == 0:
+        return None
     return round(subtotal(items) / quantity, 2)
"""


def responder_for(patch: str):
    def respond(schema, messages):
        if schema is Classification:
            return Classification(category="code_bug", needs_code_fix=True, confidence=0.9,
                                  summary="빈 주문 평균 단가 0으로 나누기", keywords=["ZeroDivisionError"])
        if schema is RootCause:
            return RootCause(root_cause="수량 0 가드 누락", suspected_files=["shopapi/orders.py"], fix_strategy="가드 복구")
        if schema is FixProposal:
            return FixProposal(patch=patch, explanation="수량 0 가드", risk="low")
        raise ValueError(schema)

    return respond


@pytest.fixture
def injected_shopapi(tmp_path, monkeypatch):
    repo = tmp_path / "shopapi"
    shutil.copytree(run_experiment.SHOPAPI, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    subprocess.run(["git", "apply", str(run_experiment.SCENARIOS / "S01.patch")], cwd=repo, check=True)
    monkeypatch.setattr(run_experiment, "SHOPAPI", repo)
    yield repo
    for name in [m for m in sys.modules if m == "shopapi" or m.startswith("shopapi.")]:
        del sys.modules[name]
    sys.path.remove(str(repo))


def run_s01(repo, tmp_path, patch: str) -> dict:
    container = Container(
        offline_settings(target_repo_path=str(repo), artifacts_dir=str(tmp_path / "artifacts")),
        model_factory=fake_model_factory(responder_for(patch), []),
    )
    api = TestClient(create_app(container), headers={"X-Automation-Token": "demo-token"})
    s = run_experiment.load_scenario("S01")

    incident = run_experiment.reproduce(s)
    accepted = run_experiment.submit(api, incident)
    final = [process_message(container, mid, p) for mid, p in container.queue.consume("t", count=1, block_ms=10)][0]
    status = run_experiment.wait(api, accepted["incident_id"], timeout_sec=0)
    row = run_experiment.summarize(s, accepted, status, run_experiment.judge(s, status["patch"]), 0)
    return {"incident": incident, "final": final, "row": row}


def test_s01_reproduces_real_traceback(injected_shopapi, tmp_path):
    incident = run_s01(injected_shopapi, tmp_path, GOOD_FIX)["incident"]
    assert incident["message"].startswith("ZeroDivisionError")
    assert "shopapi/orders.py" in incident["stacktrace"]


def test_s01_good_fix_passes_hidden_and_full_suite(injected_shopapi, tmp_path):
    result = run_s01(injected_shopapi, tmp_path, GOOD_FIX)
    assert "tests/test_orders.py" in result["final"]["test_selection"]["tests"]
    expected = {"outcome": "resolved", "category_correct": True, "attempts": 1,
                "hidden_passed": True, "full_passed": True, "false_pass": False}
    assert {k: result["row"][k] for k in expected} == expected


def test_s01_wrong_fix_is_counted_as_false_pass(injected_shopapi, tmp_path):
    row = run_s01(injected_shopapi, tmp_path, WRONG_FIX)["row"]
    assert row["outcome"] == "resolved"
    assert (row["hidden_passed"], row["full_passed"], row["false_pass"]) == (False, False, True)
