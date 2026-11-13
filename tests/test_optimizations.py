from fastapi.testclient import TestClient

from app.container import Container
from app.llm.fake import fake_model_factory
from app.main import create_app
from app.worker import process_message
from scripts.demo_offline import STACK, offline_settings
from scripts.demo_responder import demo_responder

HEADERS = {"X-Automation-Token": "demo-token"}
CODE_BUG = {"channel": "C1", "service": "checkout", "message": "500 on /orders/summary: ZeroDivisionError", "stacktrace": STACK}
INFRA = {"channel": "C1", "service": "payment", "message": "PG timeout"}


def run(tmp_path, incidents, **opts):
    calls: list = []
    c = Container(offline_settings(artifacts_dir=str(tmp_path / "artifacts"), **opts), fake_model_factory(demo_responder, calls))
    client = TestClient(create_app(c), headers=HEADERS)
    finals = []
    for inc in incidents:
        fp = client.post("/api/v1/incidents", json=inc).json()["fingerprint"]
        finals += [process_message(c, mid, p) for mid, p in c.queue.consume("t", count=10, block_ms=10)]
        c.redis.delete(f"dedupe:{fp}")
    return finals, calls, client


def tasks(calls, model):
    return [schema for name, schema in calls if name == model]


def test_llm_cache_off_calls_classifier_again(tmp_path):
    _, on, _ = run(tmp_path, [CODE_BUG, CODE_BUG])
    _, off, _ = run(tmp_path, [CODE_BUG, CODE_BUG], opt_llm_cache=False)
    assert tasks(on, "fake:light") == ["Classification"]
    assert tasks(off, "fake:light") == ["Classification", "Classification"]


def test_model_routing_off_classifies_with_heavy_model(tmp_path):
    _, calls, _ = run(tmp_path, [INFRA], opt_model_routing=False)
    assert calls == [("fake:heavy", "Classification")]


def test_early_exit_off_runs_root_cause_for_infra_incident(tmp_path):
    finals, calls, _ = run(tmp_path, [INFRA], opt_early_exit=False)
    assert calls == [("fake:light", "Classification"), ("fake:heavy", "RootCause")]
    assert finals[0]["outcome"] == "no_code_fix"


def test_test_selection_off_runs_full_suite(tmp_path):
    finals, _, _ = run(tmp_path, [CODE_BUG], opt_test_selection=False)
    assert finals[0]["test_selection"]["full_suite"] is True
    assert finals[0]["outcome"] == "resolved"


def test_fix_reuse_off_regenerates_fix_on_recurrence(tmp_path):
    finals, calls, _ = run(tmp_path, [CODE_BUG, CODE_BUG], opt_fix_reuse=False)
    assert not finals[1].get("reused_fix")
    assert tasks(calls, "fake:heavy").count("FixProposal") > 2


def test_config_endpoint_reports_optimizations(tmp_path):
    _, _, client = run(tmp_path, [], opt_early_exit=False)
    opts = client.get("/api/v1/config").json()["optimizations"]
    assert opts == {"llm_cache": True, "model_routing": True, "early_exit": False, "test_selection": True, "fix_reuse": True}
