import difflib
import shutil

from scripts import run_cluster as rc
from scripts.run_experiment import SHOPAPI, inject, load_scenario

ORDERS = "shopapi/orders.py"


def diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"))


def ai_patches(tmp_path) -> tuple[str, str]:
    """버그가 든 orders.py 기준의 패치 두 개. 버그를 되돌리는 진짜 수정과, 주석만 붙여 테스트는 통과하는 가짜 수정."""
    repo = tmp_path / "shopapi"
    shutil.copytree(SHOPAPI, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    original = (repo / ORDERS).read_text()
    inject("S01", repo)
    bugged = (repo / ORDERS).read_text()
    return diff(ORDERS, bugged, original), diff(ORDERS, bugged, bugged + "# 확인함\n")


def judged(monkeypatch, patch: str, status: str = "resolved") -> dict:
    monkeypatch.setattr(rc, "psql", lambda sql: [{"patch": patch}])
    return rc.judge_fix(load_scenario("S01"), {"id": "inc1", "status": status})


def test_real_fix_passes_hidden_test(monkeypatch, tmp_path):
    real, _ = ai_patches(tmp_path)
    assert judged(monkeypatch, real) == {"hidden_passed": True, "full_passed": True, "false_pass": False}


def test_fix_that_only_passes_visible_tests_is_a_false_pass(monkeypatch, tmp_path):
    _, fake = ai_patches(tmp_path)
    v = judged(monkeypatch, fake)
    assert v["hidden_passed"] is False and v["false_pass"] is True


def test_no_patch_is_not_judged(monkeypatch):
    assert judged(monkeypatch, "", "no_code_fix") == {"hidden_passed": None, "full_passed": None, "false_pass": None}


def test_judging_leaves_the_local_shopapi_untouched(monkeypatch, tmp_path):
    before = (SHOPAPI / ORDERS).read_text()
    judged(monkeypatch, ai_patches(tmp_path)[0])
    assert (SHOPAPI / ORDERS).read_text() == before
