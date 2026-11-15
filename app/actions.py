"""승인 후 조치(plan.md 9-8). 조치 이름과 대상은 알림과 이 허용 목록에서만 정한다. LLM 출력은 쓰지 않는다."""
from dataclasses import dataclass

from app.alerts import ALERT_KINDS


@dataclass(frozen=True)
class Action:
    name: str
    playbook: str                 # deploy/ansible/ 아래 파일
    hosts: dict[str, str]         # 허용 대상 노드 이름 -> SSH 주소. 알림 라벨의 주소는 믿지 않는다


ACTIONS: dict[str, Action] = {
    "cleanup-appdata": Action("cleanup-appdata", "cleanup-appdata.yml", {"k3s-w1": "192.168.56.11"}),
}


@dataclass(frozen=True)
class Proposal:
    action: str
    target: str

    @property
    def host(self) -> str:
        return ACTIONS[self.action].hosts[self.target]


def propose(incident: dict, outcome: str) -> Proposal | None:
    """코드 수정 대상이 아니라고 판단한 알림 장애 중, 알림 종류에 조치가 있고 대상이 허용 목록에 있는 것만."""
    kind = ALERT_KINDS.get(incident.get("alertname") or "")
    if outcome != "no_code_fix" or not kind or not kind.action:
        return None
    target = incident.get("service", "")
    return Proposal(kind.action, target) if target in ACTIONS[kind.action].hosts else None
