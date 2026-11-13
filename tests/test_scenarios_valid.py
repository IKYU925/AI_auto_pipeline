import pytest

from scripts.check_scenarios import check
from scripts.run_experiment import SCENARIOS, load_scenario

CODE_BUG_SCENARIOS = [s for s in (load_scenario(p.stem) for p in sorted(SCENARIOS.glob("S*.yaml"))) if s.type == "code_bug"]


@pytest.mark.parametrize("scenario", CODE_BUG_SCENARIOS, ids=lambda s: s.id)
def test_bug_escapes_visible_tests_and_is_caught_by_hidden_test(scenario):
    assert check(scenario) == []
