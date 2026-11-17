"""클러스터 안에서 서비스 계정 토큰으로 Kubernetes API를 부른다. 권한은 서비스 계정의 Role이 정한다."""
import os
from pathlib import Path

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
AIOPS = "aiops"
ARGOCD = "argocd"


def in_cluster() -> bool:
    return (SA_DIR / "token").exists()


class Kube:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(
            base_url="https://kubernetes.default.svc", verify=str(SA_DIR / "ca.crt"), timeout=10,
            headers={"Authorization": f"Bearer {(SA_DIR / 'token').read_text()}"},
        )

    def get(self, path: str, **params) -> dict:
        return self.http.get(path, params=params or None).raise_for_status().json()

    def own_image(self) -> str:
        return self.get(f"/api/v1/namespaces/{AIOPS}/pods/{os.environ['HOSTNAME']}")["spec"]["containers"][0]["image"]

    def create_job(self, job: dict) -> None:
        resp = self.http.post(f"/apis/batch/v1/namespaces/{AIOPS}/jobs", json=job)
        if resp.status_code != 409:  # 이미 있으면 그 Job의 결과를 기다린다(승인 처리 중 재시작)
            resp.raise_for_status()

    def job_status(self, name: str) -> dict:
        return self.get(f"/apis/batch/v1/namespaces/{AIOPS}/jobs/{name}").get("status", {})

    def job_logs(self, name: str) -> str:
        items = self.get(f"/api/v1/namespaces/{AIOPS}/pods", labelSelector=f"job-name={name}")["items"]
        if not items:
            return ""
        return self.http.get(f"/api/v1/namespaces/{AIOPS}/pods/{items[0]['metadata']['name']}/log").text

    def deployment(self, namespace: str, name: str) -> dict:
        return self.get(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}")

    def replicasets(self, namespace: str, deployment: dict) -> list[dict]:
        """이 Deployment가 만든 ReplicaSet. 같은 라벨을 쓰는 다른 Deployment 것은 소유자로 거른다."""
        selector = ",".join(f"{k}={v}" for k, v in deployment["spec"]["selector"]["matchLabels"].items())
        items = self.get(f"/apis/apps/v1/namespaces/{namespace}/replicasets", labelSelector=selector)["items"]
        uid = deployment["metadata"]["uid"]
        return [rs for rs in items if any(o.get("uid") == uid for o in rs["metadata"].get("ownerReferences", []))]

    def replace_template(self, namespace: str, name: str, template: dict) -> None:
        self.http.patch(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
                        json=[{"op": "replace", "path": "/spec/template", "value": template}],
                        headers={"Content-Type": "application/json-patch+json"}).raise_for_status()

    def application(self, name: str) -> dict:
        return self.get(f"/apis/argoproj.io/v1alpha1/namespaces/{ARGOCD}/applications/{name}")

    def set_app_images(self, name: str, images: list[str]) -> None:
        self.http.patch(f"/apis/argoproj.io/v1alpha1/namespaces/{ARGOCD}/applications/{name}",
                        json={"spec": {"source": {"kustomize": {"images": images}}}},
                        headers={"Content-Type": "application/merge-patch+json"}).raise_for_status()
