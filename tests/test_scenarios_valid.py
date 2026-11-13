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
