import json

import httpx
import pytest

from app.actions import Proposal, propose
from app.approvals import JobRunner, Kube, handle_click, job_spec
from app.notify.card import APPROVE_ACTION_ID, build_card

DISK = {"alertname": "AppdataDiskFull", "service": "k3s-w1", "message": "[AppdataDiskFull] 볼륨 90% 초과", "channel": "C1"}


class FakeRunner:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def run(self, incident_id, p):
        self.calls.append((incident_id, p))
        return self.ok, "PLAY RECAP k3s-w1 ok=3 changed=1"


class FakeSlack:
    def __init__(self):
        self.posts, self.updates = [], []

    def post(self, channel, text, thread_ts=None, blocks=None):
        self.posts.append((channel, text, thread_ts))
        return "1.0"

    def update(self, channel, ts, text, blocks):
        self.updates.append((channel, ts, blocks))


@pytest.fixture
def incident(container):
    container.__dict__["notifier"] = FakeSlack()
    container.store.create("inc-1", "fp", {**DISK, "source": "alertmanager"})
    proposal = propose(DISK, "no_code_fix")
    container.store.set_status("inc-1", "no_code_fix", proposed_action={"action": proposal.action, "target": proposal.target})
    return container


def actions(c):
    with c.db.connection() as conn:
        return conn.execute("SELECT playbook, target, approver, status, log FROM actions").fetchall()


@pytest.mark.parametrize("incident_in, outcome, expected", [
    (DISK, "no_code_fix", Proposal("cleanup-appdata", "k3s-w1")),
    ({**DISK, "service": "k3s-w2"}, "no_code_fix", None),
    (DISK, "escalated", None),
    ({**DISK, "alertname": "KubeNodeNotReady"}, "no_code_fix", None),
])
def test_only_allowlisted_action_and_target_are_proposed(incident_in, outcome, expected):
    assert propose(incident_in, outcome) == expected


def test_card_carries_only_the_incident_id_on_the_button():
    state = {"incident_id": "inc-1", "outcome": "no_code_fix", "incident": DISK,
             "proposed_action": {"action": "cleanup-appdata", "target": "k3s-w1"}}
    _, blocks = build_card(state)
    [button] = blocks[-1]["elements"]
    assert (button["action_id"], button["value"]) == (APPROVE_ACTION_ID, "inc-1")
    _, plain = build_card({**state, "proposed_action": None})
    assert all(b["type"] != "actions" for b in plain)


def test_approval_runs_once_records_the_action_and_removes_the_button(incident):
    c, runner = incident, FakeRunner()
    card = [{"type": "section"}, {"type": "actions", "block_id": "approve"}]

    assert handle_click(c, runner, "inc-1", "U-ALICE", "C1", "111.1", card) == "succeeded"
    assert handle_click(c, runner, "inc-1", "U-BOB", "C1", "111.1", card) == "already"

    assert runner.calls == [("inc-1", Proposal("cleanup-appdata", "k3s-w1"))]
    [row] = actions(c)
    assert (row["playbook"], row["target"], row["approver"], row["status"]) == ("cleanup-appdata", "k3s-w1", "U-ALICE", "succeeded")
    assert "PLAY RECAP" in row["log"]
    [(_, _, blocks)] = c.notifier.updates
    assert all(b.get("block_id") != "approve" for b in blocks), "승인 후 버튼을 지운다"
    assert [t for _, t, _ in c.notifier.posts][-1].endswith("이미 승인되어 실행했거나 실행 중인 조치입니다.")


def test_failed_job_is_recorded_as_failed(incident):
    assert handle_click(incident, FakeRunner(ok=False), "inc-1", "U-ALICE", "C1", "111.1", []) == "failed"
    assert actions(incident)[0]["status"] == "failed"


def test_unlisted_approver_and_incident_without_proposal_do_not_run(incident):
    c, runner = incident, FakeRunner()
    c.settings.action_approvers = ["U-ALICE"]
    assert handle_click(c, runner, "inc-1", "U-MALLORY", "C1", "1", []) == "not_allowed"
    c.store.create("inc-2", "fp2", {**DISK, "source": "alertmanager"})
    assert handle_click(c, runner, "inc-2", "U-ALICE", "C1", "1", []) == "no_proposal"
    assert runner.calls == [] and actions(c) == []


def test_job_targets_the_allowlisted_address_and_waits_for_the_result():
    p = Proposal("cleanup-appdata", "k3s-w1")
    script = job_spec("action-inc-1-cleanup-appdata", "reg/aiops:t", "inc-1", p)["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "-i '192.168.56.11,'" in script and script.endswith("deploy/ansible/cleanup-appdata.yml")

    seen, polls = [], iter([{}, {"succeeded": 1}])

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(409)
        if request.url.path.endswith("/jobs/action-inc-1-cleanup-appdata"):
            return httpx.Response(200, json={"status": next(polls)})
        if request.url.path.endswith("/pods"):
            return httpx.Response(200, json={"items": [{"metadata": {"name": "action-pod"}}]})
        return httpx.Response(200, text="ok=3 changed=1")

    kube = Kube(httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(handle)))
    assert JobRunner(kube, "reg/aiops:t", poll_sec=0).run("inc-1", p) == (True, "ok=3 changed=1")
    assert seen[0] == ("POST", "/apis/batch/v1/namespaces/aiops/jobs"), "이미 있는 Job(409)이면 그 결과를 기다린다"
    assert json.dumps(job_spec("n", "i", "inc-1", p)).count("aiops-node-ssh") == 1
