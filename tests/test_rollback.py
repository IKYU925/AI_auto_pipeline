import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from app.actions import Proposal, propose
from app.approvals import RolloutRunner
from app.deploys import REVISION, read_state
from app.llm import prompts
from app.kube import Kube
from app.main import create_app

UID = "dep-uid"


def rs(rev, image, created, owner=UID):
    return {"metadata": {"name": f"shopapi-{rev}", "annotations": {REVISION: str(rev)}, "creationTimestamp": created,
                         "ownerReferences": [{"uid": owner}]},
            "spec": {"template": {"metadata": {"labels": {"app": "shopapi", "pod-template-hash": f"h{rev}"}},
                                  "spec": {"containers": [{"name": "shopapi", "image": image}]}}}}


class FakeKube:
    def __init__(self, revision=4):
        self.dep = {"metadata": {"uid": UID, "annotations": {REVISION: str(revision)}, "generation": 7},
                    "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "shopapi"}}},
                    "status": {"observedGeneration": 7, "updatedReplicas": 1, "availableReplicas": 1}}
        self.sets = [rs(3, "reg/aiops:base-1", "2026-11-15T09:00:00Z"), rs(4, "reg/aiops:s01-2", "2026-11-15T10:00:00Z"),
                     rs(9, "reg/other:x", "2026-11-15T10:05:00Z", owner="someone-else")]
        self.replaced = []

    def deployment(self, ns, name):
        return copy.deepcopy(self.dep)

    def replicasets(self, ns, dep):
        return [r for r in self.sets if r["metadata"]["ownerReferences"][0]["uid"] == dep["metadata"]["uid"]]

    def replace_template(self, ns, name, template):
        self.replaced.append((ns, name, template))
        self.dep["metadata"]["annotations"][REVISION] = "5"


def incident(occurred, deploy_at="2026-11-15T10:00:00+00:00", previous=True, ns="tenant-a", alert="ShopapiException"):
    deploy = {"namespace": ns, "deployment": "shopapi", "current": {"number": 4, "image": "s01", "at": deploy_at},
              "previous": {"number": 3, "image": "base", "at": "2026-11-15T09:00:00+00:00"} if previous else None}
    return {"alertname": alert, "service": "shopapi", "occurred_at": occurred, "deploy": deploy}


@pytest.mark.parametrize("inc, outcome, expected", [
    (incident("2026-11-15T10:04:00+00:00"), "resolved", Proposal("rollout-undo", "tenant-a/shopapi", 4, 3)),
    (incident("2026-11-15T10:04:00+00:00"), "escalated", Proposal("rollout-undo", "tenant-a/shopapi", 4, 3)),
    (incident("2026-11-15T11:00:00+00:00"), "resolved", None),
    (incident("2026-11-15T09:59:00+00:00"), "resolved", None),
    (incident("2026-11-15T10:04:00+00:00", previous=False), "resolved", None),
    (incident("2026-11-15T10:04:00+00:00", ns="kube-system"), "resolved", None),
    (incident("2026-11-15T10:04:00+00:00", alert="ShopapiSlowPayment"), "no_code_fix", None),
])
def test_rollback_is_proposed_only_right_after_a_deploy_with_a_previous_revision(inc, outcome, expected):
    assert propose(inc, outcome) == expected


def test_deploy_state_reads_only_this_deployments_revisions():
    state = read_state(FakeKube(), "tenant-a", "shopapi")
    assert (state.current.number, state.current.image, state.previous.number) == (4, "reg/aiops:s01-2", 3)
    assert state.to_dict()["current"]["at"].isoformat() == "2026-11-15T10:00:00+00:00"


def test_redeploying_an_old_template_uses_the_rollout_time_not_the_replicaset_creation():
    kube = FakeKube()
    kube.dep["status"]["conditions"] = [{"type": "Progressing", "reason": "NewReplicaSetAvailable",
                                         "lastUpdateTime": "2026-11-15T12:00:00Z"}]
    assert read_state(kube, "tenant-a", "shopapi").current.at.isoformat() == "2026-11-15T12:00:00+00:00"


def test_deploy_state_lookup_failure_does_not_block_intake():
    def boom(request):
        return httpx.Response(403)

    assert read_state(Kube(httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(boom))), "shop", "shopapi") is None


def test_rollback_restores_the_previous_template_without_its_hash_label():
    kube = FakeKube()
    ok, log = RolloutRunner(kube, poll_sec=0).run("inc-1", Proposal("rollout-undo", "tenant-a/shopapi", 4, 3))
    assert ok and "rev 4 -> rev 5" in log and "base-1" in log
    [(ns, name, template)] = kube.replaced
    assert (ns, name) == ("tenant-a", "shopapi")
    assert template["metadata"]["labels"] == {"app": "shopapi"}
    assert template["spec"]["containers"][0]["image"] == "reg/aiops:base-1"


def test_rollback_refuses_when_the_deployment_moved_after_the_proposal():
    kube = FakeKube(revision=6)
    ok, log = RolloutRunner(kube, poll_sec=0).run("inc-1", Proposal("rollout-undo", "tenant-a/shopapi", 4, 3))
    assert not ok and "rev 6" in log and kube.replaced == []


def test_alert_intake_records_the_deploy_state(container):
    container.__dict__["kube"] = FakeKube()
    container.__dict__["loki"] = None
    alert = {"status": "firing", "fingerprint": "fp-1", "startsAt": "2026-11-15T10:04:00Z", "endsAt": "0001-01-01T00:00:00Z",
             "labels": {"alertname": "ShopapiException", "namespace": "tenant-a", "service": "shopapi", "severity": "critical"},
             "annotations": {"summary": "x"}}
    client = TestClient(create_app(container), headers={"Authorization": "Bearer demo-token"})
    assert client.post("/api/v1/alerts/alertmanager", json={"alerts": [alert]}).status_code == 202
    [(_, payload)] = container.queue.consume("t", count=10, block_ms=10)
    inc = payload["incident"]
    assert inc["deploy"]["current"]["number"] == 4 and inc["deploy"]["previous"]["image"] == "reg/aiops:base-1"
    assert propose(inc, "resolved") == Proposal("rollout-undo", "tenant-a/shopapi", 4, 3)


def test_root_cause_prompt_shows_the_deploy_just_before_the_incident():
    prompt = prompts.root_cause_user({**incident("2026-11-15T10:04:00+00:00"), "message": "m"}, {"category": "code_bug", "summary": "s"}, "", "")
    assert "tenant-a/shopapi 현재 리비전 4, 이미지 s01, 배포 2026-11-15T10:00:00+00:00 (장애 4분 전)" in prompt
    assert "직전 리비전 3, 이미지 base" in prompt
    assert "(배포 정보 없음)" in prompts.root_cause_user({"service": "shopapi", "message": "m"}, {"category": "x", "summary": "s"}, "", "")
