"""Slack 승인 버튼을 받아 허용 목록 조치를 Kubernetes Job으로 실행한다(plan.md 9-8).

실행: python -m app.approvals
Socket Mode라서 공개 주소가 필요 없다. 자동화가 Slack에 먼저 연결해 버튼 클릭을 받는다.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.actions import ACTIONS, Proposal
from app.notify.card import APPROVE_ACTION_ID

log = logging.getLogger("approvals")
NAMESPACE = "aiops"
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
JOB_TIMEOUT_SEC = 300


@dataclass(frozen=True)
class Decision:
    status: str                      # started | already | no_proposal | not_allowed | unknown
    proposal: Proposal | None = None


def decide(c, incident_id: str, approver: str) -> Decision:
    """실행할 조치는 버튼 값이 아니라 장애 기록에서 다시 읽고, 허용 목록으로 한 번 더 확인한다."""
    if c.settings.action_approvers and approver not in c.settings.action_approvers:
        return Decision("not_allowed")
    incident = c.store.get(incident_id)
    if incident is None:
        return Decision("unknown")
    raw = incident.get("proposed_action")
    if not raw or raw["action"] not in ACTIONS or raw["target"] not in ACTIONS[raw["action"]].hosts:
        return Decision("no_proposal")
    proposal = Proposal(raw["action"], raw["target"])
    if not c.store.start_action(incident_id, proposal.action, proposal.target, approver):
        return Decision("already", proposal)
    return Decision("started", proposal)


class Kube:
    """클러스터 안에서 서비스 계정 토큰으로 API를 부른다. 권한은 aiops의 Job 생성과 Pod 로그 읽기뿐이다."""

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(
            base_url="https://kubernetes.default.svc", verify=str(SA_DIR / "ca.crt"), timeout=10,
            headers={"Authorization": f"Bearer {(SA_DIR / 'token').read_text()}"},
        )

    def own_image(self) -> str:
        pod = self.http.get(f"/api/v1/namespaces/{NAMESPACE}/pods/{os.environ['HOSTNAME']}").raise_for_status().json()
        return pod["spec"]["containers"][0]["image"]

    def create_job(self, job: dict) -> None:
        resp = self.http.post(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs", json=job)
        if resp.status_code != 409:  # 이미 있으면 그 Job의 결과를 기다린다(승인 처리 중 재시작)
            resp.raise_for_status()

    def job_status(self, name: str) -> dict:
        return self.http.get(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{name}").raise_for_status().json().get("status", {})

    def job_logs(self, name: str) -> str:
        pods = self.http.get(f"/api/v1/namespaces/{NAMESPACE}/pods", params={"labelSelector": f"job-name={name}"})
        items = pods.raise_for_status().json()["items"]
        if not items:
            return ""
        return self.http.get(f"/api/v1/namespaces/{NAMESPACE}/pods/{items[0]['metadata']['name']}/log").text


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


REPLIES = {
    "already": "이미 승인되어 실행했거나 실행 중인 조치입니다.",
    "no_proposal": "이 장애에는 실행할 수 있는 조치가 없습니다.",
    "not_allowed": "승인 권한이 없는 사용자입니다.",
    "unknown": "장애 기록을 찾지 못했습니다.",
}


def handle_click(c, runner: JobRunner, incident_id: str, approver: str, channel: str, ts: str, blocks: list[dict]) -> str:
    decision = decide(c, incident_id, approver)
    if decision.status != "started":
        c.notifier.post(channel, f"<@{approver}> {REPLIES[decision.status]}", ts)
        return decision.status
    p = decision.proposal
    # 버튼을 지워 같은 카드에서 다시 누르지 않게 한다. 다시 눌러도 start_action이 막는다
    kept = [b for b in blocks if b.get("block_id") != "approve"]
    note = {"type": "context", "elements": [{"type": "mrkdwn", "text": f"<@{approver}> 승인 · `{p.action}` @ {p.target} 실행 중"}]}
    c.notifier.update(channel, ts, f"{p.action} 실행 중", kept + [note])
    try:
        ok, logs = runner.run(incident_id, p)
    except Exception as e:  # noqa: BLE001 - 실패도 조치 기록과 회신으로 남긴다
        log.exception("action %s for %s failed", p.action, incident_id)
        ok, logs = False, repr(e)
    c.store.finish_action(incident_id, p.action, "succeeded" if ok else "failed", logs[-8000:])
    result = "완료" if ok else "실패"
    c.notifier.post(channel, f"`{p.action}` @ {p.target} {result}\n```\n{logs[-1500:] or '-'}\n```", ts)
    return "succeeded" if ok else "failed"


def serve(c) -> None:
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    kube = Kube()
    runner = JobRunner(kube, kube.own_image())

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
