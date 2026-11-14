"""클러스터에 시나리오 장애를 넣고 감지부터 Slack 회신까지 시간을 잰다(plan.md 9-6, F12).

실행: .venv/bin/python -m scripts.run_cluster S14 S01 --max-usd 1 --out results/phase3.csv

시나리오마다
1. 자동화로 가는 firing 알림이 없을 때까지 기다리고 캐시를 비운다
2. 시나리오의 cluster 블록대로 장애를 넣는다(버그 이미지, 환경변수, 메모리 limit, 반복 요청, 노드 정지)
3. 기다리는 알림으로 접수된 장애가 끝나고 회신될 때까지 기다린다
4. 클러스터를 매니페스트 상태로 되돌리고(kubectl apply -k) 결과를 한 줄 남긴다
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

INFRA = Path(os.environ.get("INFRA_DIR", ROOT.parent))
os.environ.setdefault("KUBECONFIG", str(INFRA / "kubeconfig"))
OVERLAY = ROOT / "deploy/k8s/overlays/local"
SHOP_URL = os.environ.get("SHOP_URL", "http://shop.192.168.56.10.nip.io")
ALERTMANAGER = os.environ.get("ALERTMANAGER_URL", "http://192.168.56.10:30093")
PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://192.168.56.10:30090")
COLUMNS = [
    "scenario", "type", "alert", "incident_id", "fault_at", "occurred_at", "received_at", "finished_at", "reported_at",
    "detect_sec", "deliver_sec", "process_sec", "total_sec", "outcome", "expected_outcome", "outcome_correct",
    "category", "expected_category", "category_correct", "attempts", "usd", "llm_calls", "heavy_calls",
    "incidents_created", "other_incidents", "procedure_usd", "note",
]


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
    shopapi = next(d for d in docs if d and d["kind"] == "Deployment" and d["metadata"]["name"] == "shopapi")
    kubectl("replace", "-f", "-", stdin=yaml.safe_dump(shopapi))
    try:
        kubectl("apply", "-k", str(OVERLAY))
    except RuntimeError as e:
        print(f"  apply 재시도: {e}", file=sys.stderr)
        time.sleep(10)
        kubectl("apply", "-k", str(OVERLAY))
    for ns, deploy in (("shop", "shopapi"), ("aiops", "api"), ("aiops", "worker")):
        kubectl("-n", ns, "rollout", "status", f"deploy/{deploy}", "--timeout=300s")


def firing_automation_alerts() -> list[str]:
    alerts = httpx.get(f"{ALERTMANAGER}/api/v2/alerts", params={"active": "true", "silenced": "false",
                                                                   "inhibited": "false"}, timeout=10).json()
    return [a["labels"]["alertname"] for a in alerts if any(r["name"] == "ai-automation" for r in a["receivers"])]


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
    for ns, deploy in (("shop", "shopapi"), ("aiops", "worker"), ("aiops", "api")):
        kubectl("-n", ns, "rollout", "status", f"deploy/{deploy}", "--timeout=300s")


def send_requests(items: list[dict], shop: httpx.Client) -> None:
    for req in items:
        r = shop.request(req["method"], req["path"], json=req.get("json"), params=req.get("params"))
        print(f"  {req['method']} {req['path']} -> {r.status_code}", file=sys.stderr)


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


def wait_incident(alert: str, since: datetime, timeout_sec: float, skip: set[str] = frozenset()) -> Waited:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            rows = psql(f"""SELECT id, alertname, status, category, attempts, created_at, occurred_at, finished_at,
                                   report_ts FROM incidents
                            WHERE source = 'alertmanager' AND created_at >= '{since.isoformat()}' ORDER BY created_at""")
        except RuntimeError:
            time.sleep(10)  # 노드 장애 중에는 DB가 없다
            continue
        mine = [r for r in rows if r["alertname"] == alert and r["id"] not in skip]
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


def resume_note(incident_id: str, timeout_sec: float) -> str:
    """노드 장애 전에 처리 중이던 장애가 이어서 끝났는지. 같은 단계 호출이 2번 이상이면 그 단계를 다시 했다."""
    try:
        row = wait_status(incident_id, FINAL_STATUSES, timeout_sec)
    except TimeoutError:
        return f"재개 대상 {incident_id}: {timeout_sec:.0f}초 안에 끝나지 않음"
    calls = psql(f"SELECT task, count(*) AS n FROM llm_calls WHERE incident_id = '{incident_id}' GROUP BY task ORDER BY task")
    per_task = ",".join(f"{c['task']}x{c['n']}" for c in calls)
    return f"재개 대상 {incident_id}: {row['status']}, 회신 {'있음' if row['report_ts'] else '없음'}, 호출 {per_task}"


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


def run_scenario(s: Scenario, shop: httpx.Client, timeout_sec: float) -> dict:
    base = load_scenario(s.trigger.base) if isinstance(s.trigger, Recurrence | Duplicate) else s
    code_bug = isinstance(base.trigger, Requests)
    check_clock()
    wait_quiet()
    clear_caches()
    stop = threading.Event()
    started = now()
    try:
        if code_bug:
            deploy_bug(base.id)
        fault_at = now()
        if code_bug:
            times = s.trigger.times if isinstance(s.trigger, Duplicate) else 1
            for _ in range(times):
                send_requests(base.trigger.items, shop)
        else:
            inject_cluster_fault(s)
        if s.cluster.traffic:
            threading.Thread(target=run_traffic, args=(s.cluster.traffic, shop, stop), daemon=True).start()
        if s.cluster.node_halt:
            return run_node_halt(s, shop, started, timeout_sec)
        w = wait_incident(base.cluster.alert, fault_at, timeout_sec)
        if isinstance(s.trigger, Recurrence):
            print(f"  base {base.id} 완료({w.incident['status']}), 알림 해소 뒤 재발", file=sys.stderr)
            wait_alert_resolved(w.incident["id"])
            fault_at = now()
            send_requests(base.trigger.items, shop)
            w = wait_incident(base.cluster.alert, fault_at, timeout_sec, skip={w.incident["id"]})
        created = 1 + sum(o["alertname"] == base.cluster.alert for o in w.others)
        return {**summarize(s, fault_at, w, created), "procedure_usd": procedure_usd(started)}
    finally:
        stop.set()
        restore()


def run_node_halt(s: Scenario, shop: httpx.Client, started: datetime, timeout_sec: float) -> dict:
    h = s.cluster.node_halt
    during_id = None
    if h.during:
        during = load_scenario(h.during)
        deploy_bug(during.id)
        send_requests(during.trigger.items, shop)
        during_id = wait_incident_created(during.cluster.alert, started, timeout_sec)
        wait_status(during_id, {"running"}, timeout_sec)
        print(f"  {during.id} 장애 {during_id} 처리 중, 노드 정지", file=sys.stderr)
    fault_at = now()
    halt_node(h.vm, h.for_sec)
    note = resume_note(during_id, timeout_sec) if during_id else ""
    try:
        w = wait_incident(s.cluster.alert, fault_at, timeout_sec, skip={during_id} if during_id else frozenset())
    except TimeoutError:
        return {"scenario": s.id, "type": s.type, "alert": s.cluster.alert, "fault_at": fault_at.isoformat(),
                "outcome": "not_delivered", "expected_outcome": s.expected.outcome, "outcome_correct": False,
                "procedure_usd": procedure_usd(started), "note": f"{s.cluster.alert} 알림이 자동화에 전달되지 않음. {note}"}
    return {**summarize(s, fault_at, w, 1), "procedure_usd": procedure_usd(started), "note": note}


def wait_incident_created(alert: str, since: datetime, timeout_sec: float) -> str:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        rows = psql(f"""SELECT id FROM incidents WHERE source = 'alertmanager' AND alertname = '{alert}'
                        AND created_at >= '{since.isoformat()}' ORDER BY created_at LIMIT 1""")
        if rows:
            return rows[0]["id"]
        time.sleep(2)
    raise TimeoutError(f"{alert} 장애가 접수되지 않았다")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--max-usd", type=float, required=True, help="누적 LLM 비용이 이 금액에 닿으면 남은 시나리오를 멈춘다")
    args = ap.parse_args()

    spent = 0.0
    with httpx.Client(base_url=SHOP_URL, timeout=30) as shop:
        for sid in args.scenarios:
            if spent >= args.max_usd:
                print(f"누적 비용 ${spent:.3f}가 상한 ${args.max_usd}에 닿아 {sid}부터 멈춘다", file=sys.stderr)
                break
            print(f"== {sid}", file=sys.stderr)
            row = run_scenario(load_scenario(sid), shop, args.timeout)
            spent += row["procedure_usd"]
            print(json.dumps(row, ensure_ascii=False, default=str))
            if args.out:
                append_csv(args.out, row, COLUMNS)


if __name__ == "__main__":
    main()
