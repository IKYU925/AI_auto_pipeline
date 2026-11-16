"""Alertmanager 웹훅 묶음을 장애 접수(IncidentIn)로 바꾼다."""
from dataclasses import dataclass
from datetime import datetime

from app.schemas import IncidentIn
from app.tenants import tenant_of


@dataclass(frozen=True)
class AlertKind:
    target_labels: tuple[str, ...]
    needs_stacktrace: bool = False
    needs_pod_logs: bool = False
    action: str | None = None     # 승인 후 조치(app/actions.py의 허용 목록 이름)
    rollback: bool = False        # 배포 직후에 났으면 직전 리비전으로 롤백을 제안한다(3-10b)


# 3-4에서 실제 알림 라벨을 본 뒤 고칠 곳은 이 표 하나다(plan.md 9-3, 9-4)
ALERT_KINDS: dict[str, AlertKind] = {
    "ShopapiException": AlertKind(("service",), needs_stacktrace=True, rollback=True),
    "ShopapiHighErrorRate": AlertKind(("service",), needs_stacktrace=True, rollback=True),
    "ShopapiSlowPayment": AlertKind(("service",)),
    "KubePodCrashLooping": AlertKind(("container", "pod"), needs_pod_logs=True, rollback=True),
    "PodOOMKilled": AlertKind(("container", "pod"), needs_pod_logs=True),
    "PodImagePullFailed": AlertKind(("container", "pod"), rollback=True),
    "KubeNodeNotReady": AlertKind(("node",)),
    "AppdataDiskFull": AlertKind(("nodename",), action="cleanup-appdata"),
}
DEFAULT_KIND = AlertKind(("service", "container", "pod", "node", "namespace"))


@dataclass(frozen=True)
class Alert:
    status: str
    name: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime
    ends_at: datetime | None
    fingerprint: str

    @classmethod
    def parse(cls, raw: dict) -> "Alert":
        ends = raw.get("endsAt")
        return cls(
            status=raw["status"],
            name=raw["labels"].get("alertname", "unknown"),
            labels=raw["labels"],
            annotations=raw.get("annotations", {}),
            starts_at=datetime.fromisoformat(raw["startsAt"]),
            # 아직 해소되지 않은 알림의 endsAt은 0001-01-01이다
            ends_at=datetime.fromisoformat(ends) if ends and not ends.startswith("0001") else None,
            fingerprint=raw["fingerprint"],
        )

    @property
    def kind(self) -> AlertKind:
        return ALERT_KINDS.get(self.name, DEFAULT_KIND)

    @property
    def tenant(self) -> str:
        return tenant_of(self.labels.get("namespace"))

    @property
    def target(self) -> str:
        return next((self.labels[k] for k in self.kind.target_labels if self.labels.get(k)), "unknown")

    def to_incident(self, channel: str, stacktrace: str | None, logs: str | None = None, deploy: dict | None = None) -> IncidentIn:
        summary = self.annotations.get("summary") or self.annotations.get("description") or self.name
        return IncidentIn(
            source="alertmanager", channel=channel, service=self.target, severity=self.labels.get("severity", "unknown"),
            message=f"[{self.name}] {summary}", stacktrace=stacktrace, logs=logs, tenant=self.tenant, occurred_at=self.starts_at,
            alertname=self.name, alert_fingerprint=self.fingerprint, deploy=deploy,
        )
