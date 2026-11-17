import copy
import json

from app.actions import Proposal, propose
from app.approvals import ArgoRunner
from app.deploys import TRACKING, read_state


def entry(id_, tag, at):
    return {"id": id_, "revision": "abc123", "deployedAt": at,
            "source": {"path": "deploy/k8s/overlays/eks/shopapi-tenant-a", "kustomize": {"images": [f"aiops=ecr/aiops:{tag}"]}}}


class FakeArgo:
    """ArgoCD 자동 sync처럼, 이미지를 바꾸면 그 값으로 이력이 하나 더 쌓인다."""

    def __init__(self, history):
        self.app = {"status": {"history": history, "sync": {"status": "Synced"}, "health": {"status": "Healthy"}}}
        self.dep = {"metadata": {"annotations": {TRACKING: "shopapi-tenant-a:apps/Deployment:tenant-a/shopapi"}}}
        self.patched = []

    def deployment(self, ns, name):
        return self.dep

    def application(self, name):
        assert name == "shopapi-tenant-a"
        return copy.deepcopy(self.app)

    def set_app_images(self, name, images):
        self.patched.append((name, images))
        h = self.app["status"]["history"]
        h.append({**entry(h[-1]["id"] + 1, "x", "2026-11-17T02:10:00Z"), "source": {"kustomize": {"images": images}}})


HISTORY = [entry(1, "base-9b15467", "2026-11-17T01:30:00Z"), entry(2, "s01-7592e36", "2026-11-17T02:00:00Z")]


def test_argocd_deploy_is_proposed_as_an_application_rollback():
    state = read_state(FakeArgo(copy.deepcopy(HISTORY)), "tenant-a", "shopapi")
    assert (state.app, state.current.number, state.current.image, state.previous.number) == (
        "shopapi-tenant-a", 2, "ecr/aiops:s01-7592e36", 1)
    inc = {"alertname": "ShopapiException", "deploy": json.loads(json.dumps(state.to_dict(), default=str)), "occurred_at": "2026-11-17T02:05:00+00:00"}
    assert propose(inc, "resolved") == Proposal("argocd-rollback", "shopapi-tenant-a", 2, 1)


def test_automation_app_is_never_a_rollback_target():
    inc = {"alertname": "KubePodCrashLooping", "occurred_at": "2026-11-17T02:05:00+00:00",
           "deploy": {"namespace": "aiops", "deployment": "worker", "app": "aiops",
                      "current": {"number": 2, "image": "i", "at": "2026-11-17T02:00:00+00:00"},
                      "previous": {"number": 1, "image": "j", "at": "2026-11-17T01:30:00+00:00"}}}
    assert propose(inc, "no_code_fix") is None


def test_argocd_rollback_restores_the_previous_images_and_waits_for_the_new_deploy():
    argo = FakeArgo(copy.deepcopy(HISTORY))
    ok, log = ArgoRunner(argo, poll_sec=0).run("inc-1", Proposal("argocd-rollback", "shopapi-tenant-a", 2, 1))
    assert ok, log
    assert argo.patched == [("shopapi-tenant-a", ["aiops=ecr/aiops:base-9b15467"])]
    assert "이력 2 -> 3" in log


def test_argocd_rollback_refuses_when_another_deploy_happened_after_the_proposal():
    argo = FakeArgo(copy.deepcopy(HISTORY) + [entry(3, "s02-1", "2026-11-17T02:20:00Z")])
    ok, log = ArgoRunner(argo, poll_sec=0).run("inc-1", Proposal("argocd-rollback", "shopapi-tenant-a", 2, 1))
    assert not ok and argo.patched == []
    assert "롤백하지 않았다" in log
