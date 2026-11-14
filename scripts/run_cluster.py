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
COLUMNS = [
    "scenario", "type", "alert", "incident_id", "fault_at", "occurred_at", "received_at", "finished_at", "reported_at",
    "detect_sec", "deliver_sec", "process_sec", "total_sec", "outcome", "expected_outcome", "outcome_correct",
    "category", "expected_category", "category_correct", "attempts", "usd", "llm_calls", "heavy_calls",
    "incidents_created", "other_incidents", "procedure_usd",
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


def wait_quiet(timeout_sec: float = 900) -> None:
    deadline = time.monotonic() + timeout_sec
    while names := firing_automation_alerts():
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
    sh("vagrant", "halt", vm, cwd=INFRA)
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
        rows = psql(f"""SELECT id, alertname, status, category, attempts, created_at, occurred_at, finished_at, report_ts
                        FROM incidents WHERE source = 'alertmanager' AND created_at >= '{since.isoformat()}'
                        ORDER BY created_at""")
        mine = [r for r in rows if r["alertname"] == alert and r["id"] not in skip]
        if mine and mine[0]["status"] in FINAL_STATUSES and mine[0]["report_ts"]:
            return Waited(mine[0], [r for r in rows if r["id"] != mine[0]["id"] and r["id"] not in skip])
        time.sleep(5)
    raise TimeoutError(f"{alert} 장애가 {timeout_sec:.0f}초 안에 끝나지 않았다")


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
            halt_node(s.cluster.node_halt.vm, s.cluster.node_halt.for_sec)
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
