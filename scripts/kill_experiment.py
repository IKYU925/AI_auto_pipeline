"""워커 강제 종료 실험(plan.md 8장, 2-5).

S01을 접수하고, 지정한 LLM 작업이 llm_calls에 기록되면 그 메시지를 잡고 있는 워커 컨테이너를 docker kill로 죽인다.
남은 워커가 30초 뒤 메시지를 회수해 끝낼 때까지 기다리고, 재처리에 든 LLM 호출 수, 완료 시간, 회신 수를 남긴다.

verify-aiops 스택(가짜 LLM)에서 돌린다. 체크포인터 조건은 스택을 띄울 때 정한다:
  VERIFY_CHECKPOINT=false VERIFY_FAKE_LLM_DELAY_SEC=3 .claude/skills/verify-aiops/bin/launch.sh
  SHOPAPI_DIR=$TMPDIR/aiops-verify/$RUN_ID/targets/shopapi \\
    python -m scripts.kill_experiment --kill-after root_cause --repeat 3 --out results/phase2_kill.csv
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.db import make_pool
from app.infra import make_redis
from scripts.run_experiment import SHOPAPI, injected, load_scenario, reproduce, reset_caches, submit

PROJECT = "aiops-verify"
COLUMNS = ["started_at", "checkpoint", "kill_after", "run", "incident_id", "killed", "resumed_from", "outcome",
           "llm_calls", "llm_calls_after_kill", "heavy_calls_after_kill", "cache_hits", "elapsed_sec", "replies"]


def docker(*args: str) -> str:
    # 컨테이너 로그(python logging)는 stderr로 나오므로 둘 다 읽는다
    return subprocess.run(["docker", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout


def wait_until(fn, timeout_sec: float, interval_sec: float = 0.2):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if found := fn():
            return found
        time.sleep(interval_sec)
    raise TimeoutError(fn.__name__ if hasattr(fn, "__name__") else "condition")


def worker_containers() -> list[str]:
    return docker("ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}",
                  "--filter", "label=com.docker.compose.service=worker").split()


def one_run(api, redis, db, stream, group, kill_after: str, checkpoint: bool, heavy_model: str, run: int) -> dict:
    s = load_scenario("S01")
    reset_caches(redis, db)
    since = datetime.now(timezone.utc)
    with injected(s.id, SHOPAPI):
        started = time.monotonic()
        incident_id = submit(api, reproduce(s))["incident_id"]

        def task_recorded():
            with db.connection() as conn:
                row = conn.execute("SELECT max(id) AS id FROM llm_calls WHERE incident_id = %s AND task = %s",
                                   (incident_id, kill_after)).fetchone()
            return row["id"]

        mark = wait_until(task_recorded, 120)
        [pending] = redis.xpending_range(stream, group, "-", "+", 1)
        consumer = pending["consumer"].decode() if isinstance(pending["consumer"], bytes) else pending["consumer"]
        killed = consumer.rsplit("-", 1)[0]  # 컨테이너 호스트 이름 = 짧은 컨테이너 ID
        docker("kill", killed)

        def finished():
            st = api.get(f"/api/v1/incidents/{incident_id}").json()
            return st if st["status"] not in ("queued", "running") else None

        status = wait_until(finished, 300, 1)
        elapsed = time.monotonic() - started

    with db.connection() as conn:
        calls = conn.execute(
            """SELECT count(*) FILTER (WHERE NOT cache_hit) AS calls,
                      count(*) FILTER (WHERE NOT cache_hit AND id > %(m)s) AS after,
                      count(*) FILTER (WHERE NOT cache_hit AND id > %(m)s AND model = %(h)s) AS heavy_after,
                      count(*) FILTER (WHERE cache_hit) AS hits
               FROM llm_calls WHERE incident_id = %(i)s""", {"m": mark, "i": incident_id, "h": heavy_model}).fetchone()
    replies = sum(docker("logs", "--since", since.isoformat(), c).count("[slack disabled]")
                  for c in worker_containers() + [killed])
    docker("start", killed)
    return {
        "started_at": since.isoformat(timespec="seconds"), "checkpoint": checkpoint, "kill_after": kill_after, "run": run,
        "incident_id": incident_id, "killed": killed, "resumed_from": " ".join(status.get("resumed_from", [])),
        "outcome": status["status"], "llm_calls": calls["calls"], "llm_calls_after_kill": calls["after"],
        "heavy_calls_after_kill": calls["heavy_after"], "cache_hits": calls["hits"], "elapsed_sec": round(elapsed, 1),
        "replies": replies,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kill-after", choices=["root_cause", "generate_fix"], required=True)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--api", default="http://localhost:18000")
    ap.add_argument("--redis", default="redis://localhost:16379/0")
    ap.add_argument("--database", default="postgresql://aiops:aiops@localhost:15432/aiops")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    api = httpx.Client(base_url=args.api, headers={"X-Automation-Token": os.environ["AUTOMATION_TOKEN"]}, timeout=30)
    config = api.get("/api/v1/config").json()
    checkpoint = config["checkpoint_enabled"]
    redis, db = make_redis(args.redis), make_pool(args.database)
    for run in range(1, args.repeat + 1):
        row = one_run(api, redis, db, "incidents", "ai-workers", args.kill_after, checkpoint, config["heavy_model"], run)
        print(row, file=sys.stderr)
        if args.out:
            new = not args.out.exists()
            with args.out.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS)
                if new:
                    w.writeheader()
                w.writerow(row)
        wait_until(lambda: len(worker_containers()) == 2, 30)
    db.close()


if __name__ == "__main__":
    main()
