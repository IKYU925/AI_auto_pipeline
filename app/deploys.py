"""알림을 받은 시점의 배포 상태. 배포 직후 장애면 직전 리비전으로 롤백을 제안하는 근거가 된다(3-10b).

로컬 K3s는 Deployment의 ReplicaSet 리비전을 읽는다. ArgoCD가 배포한 Deployment(EKS)는 그 Application의 배포 이력에서
같은 모양을 채운다(plan.md 10-2). 이때 리비전 번호는 ArgoCD 이력 ID다.
"""
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import httpx

log = logging.getLogger(__name__)
REVISION = "deployment.kubernetes.io/revision"
TRACKING = "argocd.argoproj.io/tracking-id"  # "<app>:apps/Deployment:<namespace>/<name>"


@dataclass(frozen=True)
class Revision:
    number: int
    image: str
    at: datetime


@dataclass(frozen=True)
class DeployState:
    namespace: str
    deployment: str
    current: Revision
    previous: Revision | None
    app: str | None = None        # 배포한 ArgoCD Application. 있으면 롤백도 ArgoCD로 한다

    def to_dict(self) -> dict:
        return asdict(self)


def _revision(rs: dict) -> Revision:
    return Revision(int(rs["metadata"]["annotations"][REVISION]), rs["spec"]["template"]["spec"]["containers"][0]["image"],
                    datetime.fromisoformat(rs["metadata"]["creationTimestamp"]))


def _argo_revision(h: dict) -> Revision:
    images = h.get("source", {}).get("kustomize", {}).get("images", [])
    return Revision(int(h["id"]), images[0].split("=", 1)[-1] if images else h["revision"], datetime.fromisoformat(h["deployedAt"]))


def read_state(kube, namespace: str, name: str) -> DeployState | None:
    """조회에 실패해도 접수는 한다. 롤백 제안만 빠진다."""
    try:
        dep = kube.deployment(namespace, name)
        app = dep["metadata"].get("annotations", {}).get(TRACKING, "").split(":")[0] or None
        if app:
            revisions = sorted((_argo_revision(h) for h in kube.application(app)["status"].get("history", [])),
                               key=lambda r: r.number)
        else:
            revisions = sorted((_revision(rs) for rs in kube.replicasets(namespace, dep)
                                if REVISION in rs["metadata"].get("annotations", {})), key=lambda r: r.number)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("deploy state of %s/%s failed: %s", namespace, name, e)
        return None
    if not revisions:
        return None
    current = revisions[-1]
    # 같은 템플릿을 다시 배포하면 예전 ReplicaSet을 다시 쓰므로 생성 시각이 배포 시각이 아니다. 롤아웃 조건의 갱신 시각을 쓴다
    progressing = next((c for c in dep.get("status", {}).get("conditions", []) if c["type"] == "Progressing"), None)
    if not app and progressing and progressing.get("lastUpdateTime"):
        current = Revision(current.number, current.image, max(current.at, datetime.fromisoformat(progressing["lastUpdateTime"])))
    return DeployState(namespace, name, current, revisions[-2] if len(revisions) > 1 else None, app)
