import json

from scripts import run_cluster as rc


class FakeArgoCD:
    """kubectl로 본 ArgoCD. 이미지를 바꾸면 자동 sync처럼 이력이 쌓이고, sync 요청은 바로 끝난다."""

    def __init__(self):
        base = ["aiops=ecr/aiops:base-1"]
        self.apps = {name: {"spec": {"source": {"kustomize": {"images": list(base)}}},
                            "status": {"history": [{"id": 1}], "sync": {"status": "Synced"},
                                       "health": {"status": "Healthy"}, "operationState": {"startedAt": "t0"}}}
                     for name in ["aiops", *rc.SHOPAPI_APPS.values()]}
        self.set_images, self.replace_syncs = [], []

    def __call__(self, *args, stdin=None):
        if args[:2] == ("-n", "argocd") and args[2] == "get":
            return json.dumps(self.apps[args[4]])
        if args[:2] == ("-n", "argocd") and args[2] == "patch":
            app, body = self.apps[args[4]], json.loads(args[-1])
            if "spec" in body:
                app["spec"]["source"]["kustomize"]["images"] = body["spec"]["source"]["kustomize"]["images"]
                app["status"]["history"].append({"id": len(app["status"]["history"]) + 1})
            else:
                assert body["operation"]["sync"]["syncOptions"] == ["Replace=true"]
                self.replace_syncs.append((args[4], {(r["namespace"], r["name"]) for r in body["operation"]["sync"]["resources"]}))
                app["status"]["operationState"] = {"startedAt": f"t{len(self.replace_syncs)}", "phase": "Succeeded"}
            return ""
        if "set" in args:
            self.set_images.append((args[1], args[4], args[5]))
            return ""
        if "rollout" in args:
            return ""
        raise AssertionError(f"예상하지 못한 kubectl {args}")

    def images(self, app):
        return self.apps[app]["spec"]["source"]["kustomize"]["images"]


def test_eks_deploys_shopapi_bugs_through_argocd_and_restores_from_git(monkeypatch):
    argo = FakeArgoCD()

    def sh(*cmd, cwd=None, stdin=None):
        assert cmd[0] != "git", "EKS 복원은 로컬 overlay 파일을 건드리지 않는다"
        return "ecr/aiops:s20-abc"

    monkeypatch.setattr(rc, "EKS", True)
    monkeypatch.setattr(rc, "kubectl", argo)
    monkeypatch.setattr(rc, "sh", sh)
    monkeypatch.setattr(rc, "inject", lambda *a, **k: None)
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)

    rc.deploy_bug("S20")
    for app in rc.SHOPAPI_APPS.values():
        assert argo.images(app) == ["aiops=ecr/aiops:s20-abc"], "shopapi 버그는 ArgoCD 배포 이력에 남는다(롤백 대상)"
        assert len(argo.apps[app]["status"]["history"]) == 2
    assert argo.images("aiops") == ["aiops=ecr/aiops:base-1"], "자동화 본체 앱은 승인 처리 중에 바뀌지 않는다"
    assert argo.set_images == [("aiops", "deploy/api", "*=ecr/aiops:s20-abc"), ("aiops", "deploy/worker", "*=ecr/aiops:s20-abc")]

    rc.restore()
    for ns, app in rc.SHOPAPI_APPS.items():
        assert argo.images(app) == ["aiops=ecr/aiops:base-1"]
        assert (app, {(ns, "shopapi")}) in argo.replace_syncs
    assert ("aiops", {("aiops", "api"), ("aiops", "worker")}) in argo.replace_syncs


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
