"""Slack 승인 버튼을 받아 허용 목록 조치를 Kubernetes Job으로 실행한다(plan.md 9-8).

실행: python -m app.approvals
Socket Mode라서 공개 주소가 필요 없다. 자동화가 Slack에 먼저 연결해 버튼 클릭을 받는다.
"""
import copy
import logging
import threading
import time
from dataclasses import dataclass

from app.actions import ACTIONS, ArgoRollback, Playbook, Proposal, RolloutUndo, allowed
from app.deploys import REVISION
from app.kube import Kube
from app.notify.card import ALERT_RESOLVED_NOTE, APPROVE_ACTION_ID, retire_button

log = logging.getLogger("approvals")
JOB_TIMEOUT_SEC = 300
ROLLOUT_TIMEOUT_SEC = 180


@dataclass(frozen=True)
class Decision:
    status: str                      # started | already | stale | no_proposal | not_allowed | unknown
    proposal: Proposal | None = None


def decide(c, incident_id: str, approver: str) -> Decision:
    """실행할 조치는 버튼 값이 아니라 장애 기록에서 다시 읽고, 허용 목록으로 한 번 더 확인한다."""
    if c.settings.action_approvers and approver not in c.settings.action_approvers:
        return Decision("not_allowed")
    incident = c.store.get(incident_id)
    if incident is None:
        return Decision("unknown")
    raw = incident.get("proposed_action")
    proposal = Proposal(**raw) if raw else None
    if not proposal or not allowed(proposal):
        return Decision("no_proposal")
    if incident.get("alert_resolved_at"):
        return Decision("stale", proposal)
    if not c.store.start_action(incident_id, proposal.action, proposal.target, approver):
        return Decision("already", proposal)
    return Decision("started", proposal)


def job_spec(name: str, image: str, incident_id: str, p: Proposal) -> dict:
    action = ACTIONS[p.action]
    # Secret 볼륨 파일은 root 소유라 ssh가 거부한다. 실행 사용자 소유 0600으로 복사해서 쓴다
    script = (f"install -m600 /keys/ssh-privatekey /tmp/key && ansible-playbook -i '{p.host},' -u vagrant "
              f"--private-key /tmp/key deploy/ansible/{action.playbook}")
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/name": "ai-action", "aiops/incident": incident_id}},
        "spec": {
            "backoffLimit": 0, "activeDeadlineSeconds": JOB_TIMEOUT_SEC, "ttlSecondsAfterFinished": 86400,
            "template": {"spec": {
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "containers": [{"name": "ansible", "image": image, "command": ["sh", "-c", script],
                                "env": [{"name": "ANSIBLE_HOST_KEY_CHECKING", "value": "False"},
                                        {"name": "ANSIBLE_PYTHON_INTERPRETER", "value": "auto_silent"}],
                                "volumeMounts": [{"name": "keys", "mountPath": "/keys", "readOnly": True}]}],
                "volumes": [{"name": "keys", "secret": {"secretName": "aiops-node-ssh", "defaultMode": 0o444}}],
            }},
        },
    }


class JobRunner:
    def __init__(self, kube: Kube, image: str, poll_sec: float = 3):
        self.kube = kube
        self.image = image
        self.poll_sec = poll_sec

    def run(self, incident_id: str, p: Proposal) -> tuple[bool, str]:
        name = f"action-{incident_id}-{p.action}"[:63]
        self.kube.create_job(job_spec(name, self.image, incident_id, p))
        deadline = time.monotonic() + JOB_TIMEOUT_SEC + 30
        while time.monotonic() < deadline:
            status = self.kube.job_status(name)
            if status.get("succeeded") or status.get("failed"):
                return bool(status.get("succeeded")), self.kube.job_logs(name)
            time.sleep(self.poll_sec)
        return False, f"Job {name}이 {JOB_TIMEOUT_SEC}초 안에 끝나지 않았다"


class RolloutRunner:
    """kubectl rollout undo와 같다. 직전 ReplicaSet의 Pod 템플릿으로 Deployment를 되돌린다."""

    def __init__(self, kube: Kube, poll_sec: float = 3):
        self.kube = kube
        self.poll_sec = poll_sec

    def run(self, incident_id: str, p: Proposal) -> tuple[bool, str]:
        ns, name = p.target.split("/")
        dep = self.kube.deployment(ns, name)
        current = int(dep["metadata"]["annotations"][REVISION])
        if current != p.from_revision:
            return False, f"제안 뒤 배포가 바뀌었다(제안 rev {p.from_revision}, 지금 rev {current}). 롤백하지 않았다"
        target = next((rs for rs in self.kube.replicasets(ns, dep)
                       if rs["metadata"].get("annotations", {}).get(REVISION) == str(p.to_revision)), None)
        if target is None:
            return False, f"rev {p.to_revision}의 ReplicaSet이 없다(revisionHistoryLimit로 지워졌을 수 있다)"
        template = copy.deepcopy(target["spec"]["template"])
        template["metadata"].get("labels", {}).pop("pod-template-hash", None)
        self.kube.replace_template(ns, name, template)
        deadline = time.monotonic() + ROLLOUT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            d = self.kube.deployment(ns, name)
            st, want = d.get("status", {}), d["spec"]["replicas"]
            if (st.get("observedGeneration", 0) >= d["metadata"]["generation"]
                    and st.get("updatedReplicas") == want and st.get("availableReplicas") == want):
                image = template["spec"]["containers"][0]["image"]
                return True, f"{p.target} rev {current} -> rev {d['metadata']['annotations'][REVISION]}(rev {p.to_revision}의 템플릿), 이미지 {image}"
            time.sleep(self.poll_sec)
        return False, f"{p.target} 롤백이 {ROLLOUT_TIMEOUT_SEC}초 안에 끝나지 않았다"


def _images(entry: dict) -> list[str]:
    return entry.get("source", {}).get("kustomize", {}).get("images", [])


class ArgoRunner:
    """argocd app rollback과 같은 결과. ArgoCD는 자동 sync가 켜진 앱의 rollback을 거부하므로,
    Application의 이미지를 그 이력의 값으로 되돌리고 자동 sync가 배포하게 한다. 되돌린 배포도 이력에 남는다."""

    def __init__(self, kube: Kube, poll_sec: float = 3):
        self.kube = kube
        self.poll_sec = poll_sec

    def run(self, incident_id: str, p: Proposal) -> tuple[bool, str]:
        history = self.kube.application(p.target)["status"].get("history", [])
        current = history[-1]["id"] if history else None
        if current != p.from_revision:
            return False, f"제안 뒤 배포가 바뀌었다(제안 이력 {p.from_revision}, 지금 이력 {current}). 롤백하지 않았다"
        target = next((h for h in history if h["id"] == p.to_revision), None)
        if target is None:
            return False, f"{p.target}에 이력 {p.to_revision}이 없다"
        images = _images(target)
        self.kube.set_app_images(p.target, images)
        deadline = time.monotonic() + ROLLOUT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            st = self.kube.application(p.target)["status"]
            last = st.get("history", [{}])[-1]
            if (last.get("id", 0) > current and _images(last) == images
                    and st.get("sync", {}).get("status") == "Synced" and st.get("health", {}).get("status") == "Healthy"):
                return True, f"{p.target} 이력 {current} -> {last['id']}(이력 {p.to_revision}의 이미지 {', '.join(images)})"
            time.sleep(self.poll_sec)
        return False, f"{p.target} 롤백이 {ROLLOUT_TIMEOUT_SEC}초 안에 끝나지 않았다"


class Executor:
    def __init__(self, jobs: JobRunner, rollouts: RolloutRunner, argo: ArgoRunner):
        self.runners = {Playbook: jobs, RolloutUndo: rollouts, ArgoRollback: argo}

    def run(self, incident_id: str, p: Proposal) -> tuple[bool, str]:
        return self.runners[type(ACTIONS[p.action])].run(incident_id, p)


REPLIES = {
    "already": "이미 승인되어 실행했거나 실행 중인 조치입니다.",
    "stale": f"{ALERT_RESOLVED_NOTE} 실행하지 않았습니다.",
    "no_proposal": "이 장애에는 실행할 수 있는 조치가 없습니다.",
    "not_allowed": "승인 권한이 없는 사용자입니다.",
    "unknown": "장애 기록을 찾지 못했습니다.",
}


def handle_click(c, runner: Executor, incident_id: str, approver: str, channel: str, ts: str, blocks: list[dict]) -> str:
    decision = decide(c, incident_id, approver)
    if decision.status == "stale":
        c.notifier.update(channel, ts, ALERT_RESOLVED_NOTE, retire_button(blocks, ALERT_RESOLVED_NOTE))
    if decision.status != "started":
        c.notifier.post(channel, f"<@{approver}> {REPLIES[decision.status]}", ts)
        return decision.status
    p = decision.proposal
    # 버튼을 지워 같은 카드에서 다시 누르지 않게 한다. 다시 눌러도 start_action이 막는다
    c.notifier.update(channel, ts, f"{p.action} 실행 중", retire_button(blocks, f"<@{approver}> 승인 · `{p.label}` 실행 중"))
    try:
        ok, logs = runner.run(incident_id, p)
    except Exception as e:  # noqa: BLE001 - 실패도 조치 기록과 회신으로 남긴다
        log.exception("action %s for %s failed", p.action, incident_id)
        ok, logs = False, repr(e)
    c.store.finish_action(incident_id, p.action, "succeeded" if ok else "failed", logs[-8000:])
    result = "완료" if ok else "실패"
    c.notifier.post(channel, f"`{p.label}` {result}\n```\n{logs[-1500:] or '-'}\n```", ts)
    return "succeeded" if ok else "failed"


def serve(c) -> None:
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    kube = Kube()
    runner = Executor(JobRunner(kube, kube.own_image()), RolloutRunner(kube), ArgoRunner(kube))

    def on_request(client: SocketModeClient, req: SocketModeRequest) -> None:
        # 3초 안에 확인 응답을 보내야 Slack이 다시 보내지 않는다. 조치는 따로 실행한다
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type != "interactive":
            return
        payload = req.payload
        for a in payload.get("actions", []):
            if a.get("action_id") == APPROVE_ACTION_ID:
                args = (c, runner, a["value"], payload["user"]["id"], payload["channel"]["id"],
                        payload["container"]["message_ts"], payload["message"]["blocks"])
                threading.Thread(target=handle_click, args=args, daemon=True).start()

    client = SocketModeClient(app_token=c.settings.slack_app_token, web_client=WebClient(token=c.settings.slack_bot_token))
    client.socket_mode_request_listeners.append(on_request)
    client.connect()
    log.info("approvals: socket mode connected")
    threading.Event().wait()


def main() -> None:
    from app.container import Container

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    c = Container()
    if not c.settings.slack_app_token:
        # 토큰이 없으면 버튼을 받지 않는다. 종료하면 CrashLoop 알림이 자동화로 들어가므로 대기만 한다
        log.warning("SLACK_APP_TOKEN이 없어 승인 버튼을 받지 않는다")
        threading.Event().wait()
    serve(c)


if __name__ == "__main__":
    main()
