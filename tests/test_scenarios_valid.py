import pytest

from scripts.check_scenarios import check
from scripts.run_experiment import SCENARIOS, load_scenario

SCENARIO_IDS = sorted(p.stem for p in SCENARIOS.glob("S*.yaml"))


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_scenario_is_well_formed(scenario_id):
    assert check(load_scenario(scenario_id)) == []


def test_scenario_needs_exactly_one_trigger(tmp_path, monkeypatch):
    import scripts.run_experiment as rx

    (tmp_path / "SX.yaml").write_text(
        "id: SX\ntitle: t\ntype: infra\ndifficulty: easy\nexpected: {outcome: no_code_fix}\n"
        "alert: {message: m}\nstartup: {unset_env: [PG_API_KEY]}\n"
    )
    monkeypatch.setattr(rx, "SCENARIOS", tmp_path)
    with pytest.raises(ValueError, match="정확히 하나"):
        rx.load_scenario("SX")


def test_cluster_block_is_parsed_and_unknown_keys_are_rejected(tmp_path, monkeypatch):
    import scripts.run_experiment as rx

    assert load_scenario("S01").cluster == rx.Cluster(), "코드 버그는 기본값(버그 이미지와 requests)"
    base = "id: SX\ntitle: t\ntype: infra\ndifficulty: easy\nexpected: {outcome: no_code_fix}\nalert: {message: m}\n"
    (tmp_path / "SX.yaml").write_text(base + "cluster: {alert: PodOOMKilled, memory_limit: 64Mi, env: {PG_API_KEY: null},"
                                      " traffic: {method: POST, path: /orders/summary, for_sec: 30}}\n")
    (tmp_path / "SY.yaml").write_text(base.replace("SX", "SY") + "cluster: {alert: X, memroy_limit: 64Mi}\n")
    monkeypatch.setattr(rx, "SCENARIOS", tmp_path)

    c = rx.load_scenario("SX").cluster
    assert (c.alert, c.memory_limit, c.env, c.traffic.path, c.traffic.for_sec) == (
        "PodOOMKilled", "64Mi", {"PG_API_KEY": None}, "/orders/summary", 30)
    with pytest.raises(TypeError, match="memroy_limit"):
        rx.load_scenario("SY")


def test_missing_expected_runbook_is_reported(tmp_path, monkeypatch):
    import scripts.run_experiment as rx

    (tmp_path / "SX.yaml").write_text(
        "id: SX\ntitle: t\ntype: infra\ndifficulty: easy\nexpected: {outcome: no_code_fix, runbook: k8s/nope.md}\n"
        "alert: {message: m}\n"
    )
    monkeypatch.setattr(rx, "SCENARIOS", tmp_path)
    assert check(load_scenario("SX")) == ["기대 런북 k8s/nope.md가 knowledge에 없다"]
