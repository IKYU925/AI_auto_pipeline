"""n8n에 Slack 자격 증명과 장애 워크플로를 넣고 활성화한다. 다시 실행해도 같은 상태가 된다.

실행: python -m scripts.setup_n8n   (docker compose의 n8n이 떠 있어야 한다)

.env에서 읽는 값: SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_CHANNEL_ID, N8N_WEBHOOK_URL
끝나면 Slack 앱 Event Subscriptions에 넣을 Request URL을 출력한다.
"""
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "n8n" / "incident_workflow.json"
CRED_ID = "aiopsSlackBot01"
FLOW_ID = "aiopsSlackFlow01"


def read_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def compose(*args: str, user: str | None = None) -> str:
    cmd = ["docker", "compose", "exec", "-T", *(["-u", user] if user else []), "n8n", *args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def copy_in(path: Path, dest: str) -> None:
    subprocess.run(["docker", "compose", "cp", str(path), f"n8n:{dest}"], cwd=ROOT, capture_output=True, check=True)


def build_workflow(channel_id: str) -> dict:
    w = json.loads(WORKFLOW.read_text())
    trigger = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.slackTrigger")
    trigger["credentials"] = {"slackApi": {"id": CRED_ID, "name": "Slack account"}}
    trigger["parameters"]["channelId"] = {"__rl": True, "mode": "id", "value": channel_id}
    return {**w, "id": FLOW_ID, "active": False}


def main() -> None:
    env = read_env()
    missing = [k for k in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID", "N8N_WEBHOOK_URL") if not env.get(k)]
    if missing:
        raise SystemExit(f".env에 값이 없다: {missing}")

    cred = [{"id": CRED_ID, "name": "Slack account", "type": "slackApi",
             "data": {"accessToken": env["SLACK_BOT_TOKEN"], "signatureSecret": env["SLACK_SIGNING_SECRET"]}}]
    flow = build_workflow(env["SLACK_CHANNEL_ID"])
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "cred.json").write_text(json.dumps(cred))
        (Path(tmp) / "flow.json").write_text(json.dumps(flow, ensure_ascii=False))
        copy_in(Path(tmp) / "cred.json", "/tmp/cred.json")
        copy_in(Path(tmp) / "flow.json", "/tmp/flow.json")
    try:
        compose("n8n", "import:credentials", "--input=/tmp/cred.json")
        compose("n8n", "import:workflow", "--input=/tmp/flow.json")
    finally:
        # 토큰이 든 파일을 컨테이너에 남기지 않는다. docker cp로 들어간 파일은 root 소유다
        compose("rm", "-f", "/tmp/cred.json", "/tmp/flow.json", user="root")
    compose("n8n", "update:workflow", f"--id={FLOW_ID}", "--active=true")
    subprocess.run(["docker", "compose", "restart", "n8n"], cwd=ROOT, capture_output=True, check=True)

    webhook_id = next(n for n in flow["nodes"] if n["type"] == "n8n-nodes-base.slackTrigger")["webhookId"]
    print(f"Request URL: {env['N8N_WEBHOOK_URL'].rstrip('/')}/webhook/{webhook_id}/webhook")


if __name__ == "__main__":
    main()
