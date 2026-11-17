"""승인 후 조치(plan.md 9-8). 조치 이름과 대상은 알림과 이 허용 목록에서만 정한다. LLM 출력은 쓰지 않는다."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.alerts import ALERT_KINDS


@dataclass(frozen=True)
class Playbook:
    """노드 하나에 허용 목록 플레이북을 실행한다(로컬 K3s 전용, S17)."""

    playbook: str                 # deploy/ansible/ 아래 파일
    hosts: dict[str, str]         # 허용 대상 노드 이름 -> SSH 주소. 알림 라벨의 주소는 믿지 않는다


@dataclass(frozen=True)
class RolloutUndo:
    """배포 직후 장애면 Deployment를 직전 리비전으로 되돌린다(3-10b)."""

    namespaces: frozenset[str]
    window: timedelta             # 배포부터 장애 시작까지 이 안이면 배포를 원인 후보로 본다


@dataclass(frozen=True)
class ArgoRollback:
    """ArgoCD가 배포하는 환경(EKS)에서 고객사 shopapi Application을 직전 배포 이력의 이미지로 되돌린다(4-4)."""

    apps: frozenset[str]
    window: timedelta


ACTIONS: dict[str, Playbook | RolloutUndo | ArgoRollback] = {
    "cleanup-appdata": Playbook("cleanup-appdata.yml", {"k3s-w1": "192.168.56.11"}),
    "rollout-undo": RolloutUndo(frozenset({"shop", "tenant-a", "tenant-b"}), timedelta(minutes=30)),
    "argocd-rollback": ArgoRollback(frozenset({"shopapi-shop", "shopapi-tenant-a", "shopapi-tenant-b"}), timedelta(minutes=30)),
}


@dataclass(frozen=True)
class Proposal:
    action: str
    target: str                   # 노드 이름, namespace/deployment, 또는 ArgoCD Application 이름
    from_revision: int | None = None
    to_revision: int | None = None

    @property
    def host(self) -> str:
        return ACTIONS[self.action].hosts[self.target]

    @property
    def label(self) -> str:
        rev = f" (rev {self.from_revision} -> {self.to_revision})" if self.to_revision else ""
        return f"{self.action} @ {self.target}{rev}"


def allowed(p: Proposal) -> bool:
    match ACTIONS.get(p.action):
        case Playbook(hosts=hosts):
            return p.target in hosts
        case RolloutUndo(namespaces=namespaces):
            return p.target.split("/")[0] in namespaces and p.to_revision is not None
        case ArgoRollback(apps=apps):
            return p.target in apps and p.to_revision is not None
    return False


def propose(incident: dict, outcome: str) -> Proposal | None:
    kind = ALERT_KINDS.get(incident.get("alertname") or "")
    if not kind:
        return None
    if kind.action and outcome == "no_code_fix":
        p = Proposal(kind.action, incident.get("service", ""))
    elif kind.rollback and (p := _rollback(incident)):
        pass
    else:
        return None
    return p if allowed(p) else None


def _rollback(incident: dict) -> Proposal | None:
    """장애 시작 직전에 배포가 있었고 되돌릴 리비전이 있을 때만. 결과(자동 수정 여부)와 상관없이 빠른 완화로 제안한다."""
    d, occurred = incident.get("deploy"), incident.get("occurred_at")
    if not d or not d.get("previous") or not occurred:
        return None
    action, target = ("argocd-rollback", d["app"]) if d.get("app") else ("rollout-undo", f"{d['namespace']}/{d['deployment']}")
    deployed = datetime.fromisoformat(d["current"]["at"])
    if not timedelta(0) <= datetime.fromisoformat(occurred) - deployed <= ACTIONS[action].window:
        return None
    return Proposal(action, target, d["current"]["number"], d["previous"]["number"])
