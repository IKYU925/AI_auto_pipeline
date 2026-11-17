import json

from scripts import run_cluster as rc


def test_eks_deploys_bug_with_set_image_and_restores_through_argocd(monkeypatch):
    calls = []
    synced = {"done": False}

    def kubectl(*args, stdin=None):
        calls.append(args)
        if args[-1] == "jsonpath={.status.operationState.startedAt}":
            return "t0"
        if args[-1] == "json":
            state = {"startedAt": "t1", "phase": "Succeeded"} if synced["done"] else {"startedAt": "t0", "phase": "Succeeded"}
            synced["done"] = True
            return json.dumps({"status": {"operationState": state}})
        return ""

    def sh(*cmd, cwd=None, stdin=None):
        assert cmd[0] != "git", "EKS 복원은 로컬 overlay 파일을 건드리지 않는다"
        return "ecr/aiops:s01-abc"

    monkeypatch.setattr(rc, "EKS", True)
    monkeypatch.setattr(rc, "kubectl", kubectl)
    monkeypatch.setattr(rc, "sh", sh)
    monkeypatch.setattr(rc, "inject", lambda *a, **k: None)
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)

    rc.deploy_bug("S01")
    set_images = [c for c in calls if "set" in c]
    assert [(c[1], c[4]) for c in set_images] == [(ns, f"deploy/{d}") for ns, d in rc.ROLLOUTS]
    assert all(c[-1] == "*=ecr/aiops:s01-abc" for c in set_images)
    assert not any("apply" in c for c in calls), "EKS에서는 로컬 overlay를 apply하지 않는다"

    calls.clear()
    rc.restore()
    patch = next(c for c in calls if "patch" in c)
    sync = json.loads(patch[-1])["operation"]["sync"]
    assert sync["syncOptions"] == ["Replace=true"]
    assert {(r["namespace"], r["name"]) for r in sync["resources"]} == set(rc.ROLLOUTS)


def test_send_requests_retries_when_port_forward_drops(monkeypatch):
    import httpx

    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(500)

    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://shop") as shop:
        rc.send_requests([{"method": "POST", "path": "/orders/summary", "json": {"items": []}}], shop)
    assert len(attempts) == 2, "끊긴 요청을 다시 보내 앱의 500을 받는다"
