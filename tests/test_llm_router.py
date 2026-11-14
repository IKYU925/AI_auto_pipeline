import pytest

from app.llm.cost import BudgetExceeded
from app.llm.schemas import Classification, FixProposal


@pytest.fixture(autouse=True)
def incidents(container):
    for i in ("i1", "i2", "i3"):
        container.store.create(i, f"fp-{i}", {"service": "checkout", "message": "err"})


def test_task_routing_light_vs_heavy(container):
    r = container.router
    assert r.model_for("classify") == "fake:light"
    assert r.model_for("root_cause") == "fake:heavy"
    assert r.model_for("generate_fix") == "fake:heavy"


def test_classification_cached_by_fingerprint(container, llm_calls):
    r = container.router
    for _ in range(3):
        r.invoke("classify", Classification, "sys", "ZeroDivisionError", incident_id="i1", cache_key="fp-1")
    assert llm_calls == [("fake:light", "Classification")]
    assert container.cost.summary("i1")["cache_hit:classify"] == 2


def test_fix_generation_is_not_cached(container, llm_calls):
    r = container.router
    r.invoke("generate_fix", FixProposal, "sys", "same prompt", incident_id="i2")
    r.invoke("generate_fix", FixProposal, "sys", "same prompt", incident_id="i2")
    assert len(llm_calls) == 2


def test_budget_blocks_further_calls(container):
    container.cost.budget = 0.000001
    container.router.invoke("generate_fix", FixProposal, "sys", "x" * 4000, incident_id="i3")
    with pytest.raises(BudgetExceeded):
        container.router.invoke("generate_fix", FixProposal, "sys", "again", incident_id="i3")
