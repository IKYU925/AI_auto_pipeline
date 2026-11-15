"""클러스터에 시나리오 장애를 넣고 감지부터 Slack 회신까지 시간을 잰다(plan.md 9-6, F12).

실행: .venv/bin/python -m scripts.run_cluster S14 S01 --max-usd 1 --out results/phase3.csv

시나리오마다
1. 자동화로 가는 firing 알림이 없을 때까지 기다리고 캐시를 비운다
2. 시나리오의 cluster 블록대로 장애를 넣는다(버그 이미지, 환경변수, 메모리 limit, 반복 요청, 노드 정지)
3. 기다리는 알림으로 접수된 장애가 끝나고 회신될 때까지 기다린다
4. 클러스터를 매니페스트 상태로 되돌리고(kubectl apply -k) 결과를 한 줄 남긴다

3-8 실험은 시나리오 위에 옵션으로 얹는다(plan.md 9-9).
  --burst 50 S13                  실패 요청 50회가 장애 몇 건이 되는지(반복 알림 합치기)
  --kill-worker-after root_cause S02  그 단계가 끝나면 처리 중인 워커 Pod를 강제 삭제
  --halt-after classify S16       걸어 둔 장애의 그 단계가 끝나면 노드 전원 차단
  --tenants tenant-a tenant-b S01 같은 버그를 고객사마다 차례로 넣고 회신 채널과 근거 문서를 확인(plan.md 9-7)
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from app.db import make_pool
from app.infra import make_redis
from scripts.run_experiment import (
    FINAL_STATUSES,
    ROOT,
    SHOPAPI,
    Duplicate,
    Recurrence,
    Requests,
    Scenario,
    Traffic,
    append_csv,
    inject,
    load_scenario,
    reset_caches,
)

# infra_local_set은 이 저장소와 같은 폴더(~/Project) 아래의 형제 저장소다
INFRA = Path(os.environ.get("INFRA_DIR", ROOT.parent / "infra_local_set"))
os.environ.setdefault("KUBECONFIG", str(INFRA / "kubeconfig"))
OVERLAY = ROOT / "deploy/k8s/overlays/local"
CLUSTER_HOST = os.environ.get("CLUSTER_HOST", "192.168.56.10")
SHOP_URL = os.environ.get("SHOP_URL", f"http://shop.{CLUSTER_HOST}.nip.io")
ALERTMANAGER = os.environ.get("ALERTMANAGER_URL", f"http://{CLUSTER_HOST}:30093")
PROMETHEUS = os.environ.get("PROMETHEUS_URL", f"http://{CLUSTER_HOST}:30090")
TENANTS = ("tenant-a", "tenant-b")
ROLLOUTS = [("shop", "shopapi"), *((t, "shopapi") for t in TENANTS), ("aiops", "api"), ("aiops", "worker")]
COLUMNS = [
    "scenario", "type", "alert", "incident_id", "fault_at", "occurred_at", "received_at", "finished_at", "reported_at",
    "detect_sec", "deliver_sec", "process_sec", "total_sec", "outcome", "expected_outcome", "outcome_correct",
    "category", "expected_category", "category_correct", "attempts", "usd", "llm_calls", "heavy_calls",
    "incidents_created", "other_incidents", "procedure_usd", "note", "experiment",
]


@dataclass(frozen=True)
class Experiment:
    burst: int | None = None
    kill_worker_after: str | None = None
    halt_after: str | None = None
    tag: str = ""

    @property
    def label(self) -> str:
        parts = [self.tag, f"burst{self.burst}" if self.burst else "",
                 f"kill_worker@{self.kill_worker_after}" if self.kill_worker_after else "",
                 f"halt@{self.halt_after}" if self.halt_after else ""]
        return "+".join(p for p in parts if p)


def sh(*cmd: str, cwd: Path | None = None, stdin: str | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} 실패({r.returncode}): {r.stderr.strip()[-1000:]}")
    return r.stdout.strip()


def kubectl(*args: str, stdin: str | None = None) -> str:
    return sh("kubectl", *args, stdin=stdin)


def psql(sql: str) -> list[dict]:
    out = kubectl("-n", "aiops", "exec", "postgres-0", "--", "psql", "-U", "aiops", "-d", "aiops", "-tAc",
                  f"SELECT coalesce(json_agg(t), '[]') FROM ({sql}) t")
    return json.loads(out)


def now() -> datetime:
    return datetime.now(timezone.utc)


def restore() -> None:
    """kubectl apply는 set env, set resources로 더한 필드를 지우지 않는다. shopapi Deployment는 매니페스트로 통째로 바꾼다."""
    sh("git", "checkout", "--", str(OVERLAY / "kustomization.yaml"), cwd=ROOT)
    docs = yaml.safe_load_all(kubectl("kustomize", str(OVERLAY)))
    for shopapi in (d for d in docs if d and d["kind"] == "Deployment" and d["metadata"]["name"] == "shopapi"):
        kubectl("replace", "-f", "-", stdin=yaml.safe_dump(shopapi))
    try:
        kubectl("apply", "-k", str(OVERLAY))
    except RuntimeError as e:
        print(f"  apply 재시도: {e}", file=sys.stderr)
        time.sleep(10)
        kubectl("apply", "-k", str(OVERLAY))
    for ns, deploy in ROLLOUTS:
        kubectl("-n", ns, "rollout", "status", f"deploy/{deploy}", "--timeout=300s")


def automation_alerts(inhibited: bool = False) -> list[dict]:
    alerts = httpx.get(f"{ALERTMANAGER}/api/v2/alerts", params={"active": "true", "silenced": "false",
                                                                   "inhibited": str(inhibited).lower()}, timeout=10).json()
    return [a for a in alerts if any(r["name"] == "ai-automation" for r in a["receivers"])]


def firing_automation_alerts() -> list[str]:
    return [a["labels"]["alertname"] for a in automation_alerts()]


def watch_alerts(seen: dict[str, str], stop: threading.Event) -> None:
    """반복 알림 합치기: 실행 동안 자동화로 가는 알림을 지문별로 모은다. 억제된 알림은 웹훅으로 가지 않는다."""
    while not stop.is_set():
        try:
            for a in automation_alerts(inhibited=True):
                state = "억제" if a["status"]["inhibitedBy"] else "전송"
                seen[a["fingerprint"]] = f"{a['labels']['alertname']}({state})"
        except httpx.HTTPError:
            pass
        stop.wait(5)


def webhook_sends() -> float:
    r = httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": 'sum(alertmanager_notifications_total{integration="webhook"})'},
                  timeout=10).json()["data"]["result"]
    return float(r[0]["value"][1]) if r else 0.0


def check_clock(max_skew_sec: float = 5) -> None:
    """맥이 잠들면 VM이 멈췄다가 시계가 늦은 채로 깨어날 수 있다. 그러면 시각 차이로 재는 값이 모두 틀린다."""
    # 수신, 완료 시각은 DB(k3s-w1)가, 알림 시작 시각은 Prometheus(k3s-w2)가 찍는다
    clocks = {
        "DB(k3s-w1)": float(psql("SELECT extract(epoch FROM now()) AS epoch")[0]["epoch"]),
        "Prometheus(k3s-w2)": float(httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": "time()"},
                                              timeout=10).json()["data"]["result"][0]),
    }
    for name, t in clocks.items():
        if abs(skew := t - time.time()) > max_skew_sec:
            raise RuntimeError(f"{name} 시계가 이 컴퓨터와 {skew:.0f}초 어긋난다. 노드 시계를 맞춘 뒤 다시 실행한다")


def open_alert_incidents() -> list[str]:
    """해소 알림을 아직 못 받은 장애. 같은 알림이 다시 뜨면 새 장애가 아니라 이 장애에 묶인다."""
    rows = psql("""SELECT alertname FROM incidents WHERE source = 'alertmanager' AND alert_resolved_at IS NULL
                   AND created_at > now() - interval '3 hours'""")
    return [r["alertname"] for r in rows]


def wait_quiet(timeout_sec: float = 900) -> None:
    """Alertmanager에 firing 알림이 없고, 자동화도 이전 알림의 해소를 받았을 때까지 기다린다.
    Alertmanager에서 풀린 직후 다음 장애를 넣으면 같은 알림이 다시 이어져 새 장애로 접수되지 않는다(S02에서 확인)."""
    deadline = time.monotonic() + timeout_sec
    while names := firing_automation_alerts() + open_alert_incidents():
        if time.monotonic() > deadline:
            raise TimeoutError(f"자동화로 가는 알림이 풀리지 않는다: {names}")
        print(f"  알림 해소 대기: {names}", file=sys.stderr)
        time.sleep(15)


@contextmanager
def port_forward(svc: str, local: int, remote: int) -> Iterator[None]:
    proc = subprocess.Popen(["kubectl", "-n", "aiops", "port-forward", f"svc/{svc}", f"{local}:{remote}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        yield
    finally:
        proc.terminate()


def clear_caches() -> None:
    pw = sh("sh", "-c", "kubectl -n aiops get secret aiops-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d")
    with port_forward("redis", 16380, 6379), port_forward("postgres", 15433, 5432):
        db = make_pool(f"postgresql://aiops:{pw}@localhost:15433/aiops")
        try:
            reset_caches(make_redis("redis://localhost:16380/0"), db)
        finally:
            db.close()


def deploy_bug(scenario_id: str) -> None:
    inject(scenario_id, SHOPAPI)
    try:
        image = sh(str(ROOT / "scripts/build_push.sh"), scenario_id.lower(), cwd=ROOT).splitlines()[-1]
    finally:
        inject(scenario_id, SHOPAPI, revert=True)
    print(f"  버그 이미지 {image}", file=sys.stderr)
    kubectl("apply", "-k", str(OVERLAY))
    for ns, deploy in ROLLOUTS:
        kubectl("-n", ns, "rollout", "status", f"deploy/{deploy}", "--timeout=300s")


GATEWAY_ERRORS = {502, 503, 504}


def send_requests(items: list[dict], shop: httpx.Client) -> None:
    """배포 직후에는 ingress가 아직 새 Pod로 연결하지 못해 502가 난다(S08에서 확인). 앱의 응답이 올 때까지 다시 보낸다."""
    for req in items:
        for _ in range(10):
            r = shop.request(req["method"], req["path"], json=req.get("json"), params=req.get("params"))
            print(f"  {req['method']} {req['path']} -> {r.status_code}", file=sys.stderr)
            if r.status_code not in GATEWAY_ERRORS:
                break
            time.sleep(3)
        else:
            raise RuntimeError(f"{req['path']}가 계속 {r.status_code}로 끝나 장애를 넣지 못했다")


def run_traffic(t: Traffic, shop: httpx.Client, stop: threading.Event) -> None:
    body = t.body()
    end = time.monotonic() + t.for_sec
    while time.monotonic() < end and not stop.is_set():
        try:
            shop.request(t.method, t.path, json=body)
        except httpx.HTTPError:
            pass
        stop.wait(t.every_sec)


def halt_node(vm: str, for_sec: float) -> None:
    """전원을 바로 끈다. 정상 종료면 워커가 SIGTERM을 받고 하던 일을 마칠 수 있어 장애가 아니다."""
    sh("vagrant", "halt", "--force", vm, cwd=INFRA)
    print(f"  {vm} 정지, {for_sec:.0f}초 뒤 기동", file=sys.stderr)
    time.sleep(for_sec)
    sh("vagrant", "up", vm, cwd=INFRA)


def inject_cluster_fault(s: Scenario) -> None:
    c = s.cluster
    if c.env:
        kubectl("-n", "shop", "set", "env", "deploy/shopapi",
                *(f"{k}-" if v is None else f"{k}={v}" for k, v in c.env.items()))
    if c.memory_limit:
        kubectl("-n", "shop", "set", "resources", "deploy/shopapi", f"--limits=memory={c.memory_limit}")


@dataclass
class Waited:
    incident: dict
    others: list[dict]


def wait_incident(alert: str, since: datetime, timeout_sec: float, skip: set[str] = frozenset(),
                  tenant: str | None = None) -> Waited:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            rows = psql(f"""SELECT id, alertname, status, category, attempts, created_at, occurred_at, finished_at,
                                   report_ts, tenant FROM incidents
                            WHERE source = 'alertmanager' AND created_at >= '{since.isoformat()}' ORDER BY created_at""")
        except RuntimeError:
            time.sleep(10)  # 노드 장애 중에는 DB가 없다
            continue
        mine = [r for r in rows if r["alertname"] == alert and r["id"] not in skip and tenant in (None, r["tenant"])]
        if mine and mine[0]["status"] in FINAL_STATUSES and mine[0]["report_ts"]:
            return Waited(mine[0], [r for r in rows if r["id"] != mine[0]["id"] and r["id"] not in skip])
        time.sleep(5)
    raise TimeoutError(f"{alert} 장애가 {timeout_sec:.0f}초 안에 끝나지 않았다")


def wait_status(incident_id: str, statuses: set[str], timeout_sec: float) -> dict:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            rows = psql(f"SELECT id, status, report_ts FROM incidents WHERE id = '{incident_id}'")
        except RuntimeError:
            rows = []
        if rows and rows[0]["status"] in statuses:
            return rows[0]
        time.sleep(1)
    raise TimeoutError(f"{incident_id}가 {sorted(statuses)} 상태가 되지 않았다")


def wait_llm_call(incident_id: str, task: str, timeout_sec: float) -> None:
    deadline = time.monotonic() + timeout_sec
    while not psql(f"SELECT 1 FROM llm_calls WHERE incident_id = '{incident_id}' AND task = '{task}'"):
        if time.monotonic() > deadline:
            raise TimeoutError(f"{incident_id}의 {task} 호출이 {timeout_sec:.0f}초 안에 없다")
        time.sleep(0.5)


def kill_holding_worker() -> str:
    """처리 중인 메시지를 가진 워커 Pod를 강제로 지운다. 소비자 이름은 '<Pod 이름>-<pid>'다(app/worker.py)."""
    out = kubectl("-n", "aiops", "exec", "redis-0", "--", "redis-cli", "XPENDING", "incidents", "ai-workers", "-", "+", "10")
    lines = out.splitlines()
    consumers = {lines[i + 1] for i in range(0, len(lines) - 3, 4)}
    if len(consumers) != 1:
        raise RuntimeError(f"처리 중인 워커를 하나로 정하지 못했다: {out!r}")
    pod = consumers.pop().rsplit("-", 1)[0]
    kubectl("-n", "aiops", "delete", "pod", pod, "--force", "--grace-period=0")
    print(f"  워커 {pod} 강제 삭제", file=sys.stderr)
    return pod


def slack_messages(since: datetime, tenant: str = "common") -> int:
    """고객사 회신 채널(없으면 공통 채널)에 올라온 메시지 수. 회신 카드와 패치 메시지가 같은 장애에 두 번 가면 중복 회신이다."""
    code = ("import os,json,httpx;"
            f"ch=json.loads(os.environ.get('TENANT_SLACK_CHANNELS') or '{{}}').get('{tenant}',os.environ['ALERT_SLACK_CHANNEL']);"
            "r=httpx.get('https://slack.com/api/conversations.history',"
            "headers={'Authorization':'Bearer '+os.environ['SLACK_BOT_TOKEN']},"
            f"params={{'channel':ch,'oldest':'{since.timestamp()}','limit':100}}).json();"
            "print(len(r['messages']))")
    return int(kubectl("-n", "aiops", "exec", "deploy/api", "-c", "api", "--", "python", "-c", code))


def resume_note(incident_id: str, interrupted_at: datetime, timeout_sec: float) -> str:
    """중단된 장애가 이어서 끝났는지. 같은 단계 호출이 2번 이상이면 그 단계를 다시 했다."""
    try:
        row = wait_status(incident_id, FINAL_STATUSES, timeout_sec)
    except TimeoutError:
        return f"재개 대상 {incident_id}: {timeout_sec:.0f}초 안에 끝나지 않음"
    calls = psql(f"""SELECT task, count(*) AS n, min(created_at) FILTER (WHERE created_at > '{interrupted_at.isoformat()}') AS after
                     FROM llm_calls WHERE incident_id = '{incident_id}' GROUP BY task ORDER BY min(created_at)""")
    per_task = ",".join(f"{c['task']}x{c['n']}" for c in calls)
    first_after = min((ts(c["after"]) for c in calls if c["after"]), default=None)
    info = psql(f"SELECT created_at, finished_at, result->'resumed_from' AS resumed_from FROM incidents WHERE id = '{incident_id}'")[0]
    time.sleep(10)  # 두 번째 회신이 있다면 첫 회신 직후에 온다
    expected = 2 if row["status"] == "resolved" else 1
    return (f"재개 대상 {incident_id}: {row['status']}, 재개 단계 {info['resumed_from']}, 호출 {per_task}, "
            f"중단 뒤 첫 호출까지 {seconds(interrupted_at, first_after)}초, 중단 뒤 완료까지 {seconds(interrupted_at, ts(info['finished_at']))}초, "
            f"Slack 메시지 {slack_messages(ts(info['created_at']))}개(중복 없으면 {expected}개)")


def wait_alert_resolved(incident_id: str, timeout_sec: float = 600) -> None:
    deadline = time.monotonic() + timeout_sec
    while not psql(f"SELECT 1 FROM incidents WHERE id = '{incident_id}' AND alert_resolved_at IS NOT NULL"):
        if time.monotonic() > deadline:
            raise TimeoutError(f"{incident_id} 알림이 해소되지 않았다")
        time.sleep(10)


def ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def seconds(a: datetime | None, b: datetime | None) -> float | None:
    return round((b - a).total_seconds(), 1) if a and b else None


def procedure_usd(since: datetime) -> float:
    """재발의 base 처리나 함께 뜬 다른 알림까지, 절차 동안 접수된 모든 장애의 LLM 비용."""
    rows = psql(f"""SELECT coalesce(sum(c.usd), 0) AS usd FROM llm_calls c JOIN incidents i ON i.id = c.incident_id
                    WHERE i.source = 'alertmanager' AND i.created_at >= '{since.isoformat()}'""")
    return round(float(rows[0]["usd"]), 6)


def summarize(s: Scenario, fault_at: datetime, w: Waited, created: int) -> dict:
    i = w.incident
    calls = psql(f"SELECT model, usd FROM llm_calls WHERE incident_id = '{i['id']}'")
    occurred, received, finished = ts(i["occurred_at"]), ts(i["created_at"]), ts(i["finished_at"])
    reported = datetime.fromtimestamp(float(i["report_ts"]), timezone.utc)
    return {
        "scenario": s.id, "type": s.type, "alert": s.cluster.alert, "incident_id": i["id"],
        "fault_at": fault_at.isoformat(), "occurred_at": i["occurred_at"], "received_at": i["created_at"],
        "finished_at": i["finished_at"], "reported_at": reported.isoformat(),
        "detect_sec": seconds(fault_at, occurred), "deliver_sec": seconds(occurred, received),
        "process_sec": seconds(received, finished), "total_sec": seconds(fault_at, reported),
        "outcome": i["status"], "expected_outcome": s.expected.outcome,
        "outcome_correct": i["status"] == s.expected.outcome,
        "category": i["category"], "expected_category": s.expected.category,
        "category_correct": None if s.expected.category is None else i["category"] == s.expected.category,
        "attempts": i["attempts"], "usd": round(sum(float(c["usd"] or 0) for c in calls), 6),
        "llm_calls": len(calls), "heavy_calls": sum("opus" in c["model"] for c in calls),
        "incidents_created": created,
        "other_incidents": ";".join(f"{o['alertname']}:{o['status']}" for o in w.others),
    }


def run_scenario(s: Scenario, shop: httpx.Client, timeout_sec: float, exp: Experiment = Experiment()) -> dict:
    base = load_scenario(s.trigger.base) if isinstance(s.trigger, Recurrence | Duplicate) else s
    code_bug = isinstance(base.trigger, Requests)
    check_clock()
    wait_quiet()
    clear_caches()
    stop = threading.Event()
    started = now()
    alerts_seen: dict[str, str] = {}
    if exp.burst:
        threading.Thread(target=watch_alerts, args=(alerts_seen, stop), daemon=True).start()
        sends_before = webhook_sends()
    try:
        if code_bug:
            deploy_bug(base.id)
        fault_at = now()
        if code_bug:
            times = exp.burst or (s.trigger.times if isinstance(s.trigger, Duplicate) else 1)
            for _ in range(times):
                send_requests(base.trigger.items, shop)
            requests_sec = seconds(fault_at, now())
        else:
            inject_cluster_fault(s)
        if s.cluster.traffic:
            threading.Thread(target=run_traffic, args=(s.cluster.traffic, shop, stop), daemon=True).start()
        if s.cluster.node_halt:
            return run_node_halt(s, shop, started, timeout_sec, exp)
        if s.cluster.disk_fill:
            return run_disk_fill(s, started, timeout_sec, exp)
        note = ""
        if exp.kill_worker_after:
            incident_id = wait_incident_created(base.cluster.alert, fault_at, timeout_sec)
            wait_llm_call(incident_id, exp.kill_worker_after, timeout_sec)
            kill_holding_worker()
            note = resume_note(incident_id, now(), timeout_sec)
        w = wait_incident(base.cluster.alert, fault_at, timeout_sec)
        if isinstance(s.trigger, Recurrence):
            print(f"  base {base.id} 완료({w.incident['status']}), 알림 해소 뒤 재발", file=sys.stderr)
            wait_alert_resolved(w.incident["id"])
            # 중복 제거 기간(10분)이 지났다고 보고 같은 장애를 다시 받게 한다. 검증된 수정안은 남긴다(run_experiment와 같음)
            fp = psql(f"SELECT fingerprint FROM incidents WHERE id = '{w.incident['id']}'")[0]["fingerprint"]
            kubectl("-n", "aiops", "exec", "redis-0", "--", "redis-cli", "DEL", f"dedupe:{fp}")
            fault_at = now()
            send_requests(base.trigger.items, shop)
            w = wait_incident(base.cluster.alert, fault_at, timeout_sec, skip={w.incident["id"]})
        if s.cluster.approve:
            note = approved_action_note(w.incident)
        created = 1 + sum(o["alertname"] == base.cluster.alert for o in w.others)
        if exp.burst:
            wait_alert_resolved(w.incident["id"])
            note = (f"실패 요청 {times}회({requests_sec}초), 자동화로 간 알림 {len(alerts_seen)}개"
                    f"({', '.join(sorted(alerts_seen.values()))}), 웹훅 전송 {webhook_sends() - sends_before:.0f}회, 장애 {created}건")
        return {**summarize(s, fault_at, w, created), "procedure_usd": procedure_usd(started), "note": note,
                "experiment": exp.label}
    finally:
        stop.set()
        restore()


def run_node_halt(s: Scenario, shop: httpx.Client, started: datetime, timeout_sec: float, exp: Experiment) -> dict:
    h = s.cluster.node_halt
    during_id = None
    if h.during:
        during = load_scenario(h.during)
        deploy_bug(during.id)
        send_requests(during.trigger.items, shop)
        during_id = wait_incident_created(during.cluster.alert, started, timeout_sec)
        wait_status(during_id, {"running"}, timeout_sec)
        if exp.halt_after:
            wait_llm_call(during_id, exp.halt_after, timeout_sec)
        print(f"  {during.id} 장애 {during_id} 처리 중, 노드 정지", file=sys.stderr)
    fault_at = now()
    halt_node(h.vm, h.for_sec)
    note = resume_note(during_id, fault_at, timeout_sec) if during_id else ""
    try:
        w = wait_incident(s.cluster.alert, fault_at, timeout_sec, skip={during_id} if during_id else frozenset())
    except TimeoutError:
        return {"scenario": s.id, "type": s.type, "alert": s.cluster.alert, "fault_at": fault_at.isoformat(),
                "outcome": "not_delivered", "expected_outcome": s.expected.outcome, "outcome_correct": False,
                "procedure_usd": procedure_usd(started), "note": f"{s.cluster.alert} 알림이 자동화에 전달되지 않음. {note}",
                "experiment": exp.label}
    return {**summarize(s, fault_at, w, 1), "procedure_usd": procedure_usd(started), "note": note, "experiment": exp.label}


def approved_action_note(incident: dict, approve_timeout_sec: float = 900) -> str:
    """카드의 승인 버튼을 사람이 누르고 조치가 끝나 알림이 해소될 때까지 기다리고 구간별 시간을 적는다(plan.md 9-9)."""
    proposal = psql(f"SELECT result->'proposed_action' AS p FROM incidents WHERE id = '{incident['id']}'")[0]["p"]
    if not proposal:
        return "승인 요청(조치 제안)이 없는 카드"
    print(f"  {incident['id']} 카드에 승인 버튼({proposal['action']} @ {proposal['target']}). "
          f"Slack에서 승인하세요({approve_timeout_sec:.0f}초 대기)", file=sys.stderr)
    deadline = time.monotonic() + approve_timeout_sec
    while not (rows := psql(f"""SELECT approver, approved_at, status, finished_at FROM actions
                                WHERE incident_id = '{incident['id']}' AND finished_at IS NOT NULL""")):
        if time.monotonic() > deadline:
            raise TimeoutError("승인되지 않았거나 조치가 끝나지 않았다")
        time.sleep(5)
    action = rows[0]
    wait_alert_resolved(incident["id"])
    resolved = ts(psql(f"SELECT alert_resolved_at FROM incidents WHERE id = '{incident['id']}'")[0]["alert_resolved_at"])
    reported = datetime.fromtimestamp(float(incident["report_ts"]), timezone.utc)
    approved = ts(action["approved_at"])
    return (f"조치 {proposal['action']} @ {proposal['target']}: 알림부터 승인 요청까지 {seconds(ts(incident['occurred_at']), reported)}초, "
            f"승인 대기 {seconds(reported, approved)}초, 승인부터 조치 끝까지 {seconds(approved, ts(action['finished_at']))}초, "
            f"승인부터 알림 해소까지 {seconds(approved, resolved)}초, 조치 {action['status']}, 승인자 {action['approver']}")


def run_disk_fill(s: Scenario, started: datetime, timeout_sec: float, exp: Experiment) -> dict:
    d = s.cluster.disk_fill
    fill = f"sudo mkdir -p {Path(d.path).parent} && sudo fallocate -l {d.size} {d.path} && df -h {Path(d.path).parent}"
    fault_at = now()
    try:
        sh("vagrant", "ssh", d.vm, "-c", fill, cwd=INFRA)
        w = wait_incident(s.cluster.alert, fault_at, timeout_sec)
        return {**summarize(s, fault_at, w, 1), "procedure_usd": procedure_usd(started),
                "note": approved_action_note(w.incident), "experiment": exp.label}
    finally:
        sh("vagrant", "ssh", d.vm, "-c", f"sudo rm -f {d.path}", cwd=INFRA)


def wait_incident_created(alert: str, since: datetime, timeout_sec: float) -> str:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        rows = psql(f"""SELECT id FROM incidents WHERE source = 'alertmanager' AND alertname = '{alert}'
                        AND created_at >= '{since.isoformat()}' ORDER BY created_at LIMIT 1""")
        if rows:
            return rows[0]["id"]
        time.sleep(2)
    raise TimeoutError(f"{alert} 장애가 접수되지 않았다")


def run_tenants(s: Scenario, timeout_sec: float) -> list[dict]:
    """같은 코드 버그를 고객사마다 차례로 넣는다. 두 번째 고객사는 첫 장애의 중복 제거 기간(10분) 안에 들어간다."""
    if not isinstance(s.trigger, Requests):
        raise ValueError(f"{s.id}는 코드 버그 시나리오가 아니다")
    check_clock()
    wait_quiet()
    clear_caches()
    started = now()
    waited: list[tuple[str, datetime, Waited]] = []
    try:
        deploy_bug(s.id)
        for t in TENANTS:
            with httpx.Client(base_url=f"http://{t}.{CLUSTER_HOST}.nip.io", timeout=30) as shop:
                fault_at = now()
                send_requests(s.trigger.items, shop)
            waited.append((t, fault_at, wait_incident(s.cluster.alert, fault_at, timeout_sec, tenant=t)))
        time.sleep(10)  # 다른 채널로 잘못 간 회신이 있다면 처리 직후에 온다
        messages = {t: slack_messages(started, t) for t in (*TENANTS, "common")}
        rows = []
        for t, fault_at, w in waited:
            docs = psql(f"SELECT result->'context_doc_ids' AS ids FROM incidents WHERE id = '{w.incident['id']}'")[0]["ids"] or []
            foreign = [d for d in docs if any(o in d for o in TENANTS if o != t)]
            note = (f"고객사 {t}, 실행 전체의 채널별 메시지 "
                    f"{', '.join(f'{c} {n}개' for c, n in messages.items())}(장애마다 카드와 패치 2개), "
                    f"근거 문서 {sorted(set(docs))}, 다른 고객사 문서 {len(foreign)}개")
            rows.append({**summarize(s, fault_at, w, 1), "note": note, "experiment": f"tenant:{t}"})
        rows[-1]["procedure_usd"] = procedure_usd(started)
        return rows
    finally:
        restore()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--max-usd", type=float, required=True, help="누적 LLM 비용이 이 금액에 닿으면 남은 시나리오를 멈춘다")
    ap.add_argument("--burst", type=int, help="중복 시나리오(S13)의 실패 요청 수")
    ap.add_argument("--kill-worker-after", metavar="TASK", help="이 단계 LLM 호출이 끝나면 처리 중인 워커 Pod를 강제 삭제")
    ap.add_argument("--halt-after", metavar="TASK", help="노드 장애(S16) 전에 걸어 둔 장애의 이 단계가 끝나면 전원 차단")
    ap.add_argument("--tenants", action="store_true", help="코드 버그 시나리오 하나를 고객사(tenant-a, tenant-b)마다 넣는다")
    ap.add_argument("--tag", default="", help="CSV experiment 열에 남길 이름(예: 3-8 분류 개선 뒤 재실행)")
    args = ap.parse_args()
    exp = Experiment(args.burst, args.kill_worker_after, args.halt_after, args.tag)

    spent = 0.0
    with httpx.Client(base_url=SHOP_URL, timeout=30) as shop:
        for sid in args.scenarios:
            if spent >= args.max_usd:
                print(f"누적 비용 ${spent:.3f}가 상한 ${args.max_usd}에 닿아 {sid}부터 멈춘다", file=sys.stderr)
                break
            print(f"== {sid}", file=sys.stderr)
            rows = (run_tenants(load_scenario(sid), args.timeout) if args.tenants
                    else [run_scenario(load_scenario(sid), shop, args.timeout, exp)])
            for row in rows:
                spent += row.get("procedure_usd") or 0
                print(json.dumps(row, ensure_ascii=False, default=str))
                if args.out:
                    append_csv(args.out, row, COLUMNS)


if __name__ == "__main__":
    main()
