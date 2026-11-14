"""장애 시나리오를 실행하고 결과를 판정해 CSV로 남긴다.

실행: python -m scripts.run_experiment S01 S02 --condition A --repeat 3 --out results/phase1_A.csv

1. 서버의 최적화 설정이 --condition(A: 모두 켬, B: 모두 끔)과 같은지 확인한다
2. 실행마다 캐시를 비운다
3. 시나리오 버그를 shopapi에 주입한다(워커의 /repo와 같은 폴더)
4. 버그가 든 shopapi로 장애를 재현해 자동화 API로 접수하고 완료될 때까지 기다린다
5. AI 패치를 버그가 들어간 복사본에 적용하고 숨은 테스트와 전체 테스트로 판정한다
6. 버그를 원복하고 결과를 CSV에 한 줄 붙인다
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from psycopg_pool import ConnectionPool
from redis import Redis

from app.core.store import FixStore
from app.db import make_pool
from app.infra import make_redis
from app.testing.runner import SandboxTestRunner

ROOT = Path(__file__).resolve().parents[1]
# 검증 스택은 사용 중인 스택과 소스를 나누려고 shopapi 복사본 경로를 넘긴다
SHOPAPI = Path(os.environ.get("SHOPAPI_DIR", ROOT / "targets" / "shopapi"))
SCENARIOS = ROOT / "scenarios"
FINAL_STATUSES = {"resolved", "escalated", "no_code_fix", "budget_exceeded", "failed"}


@dataclass(frozen=True)
class Expected:
    outcome: str
    category: str | None = None
    needs_code_fix: bool | None = None
    root_cause_file: str | None = None
    llm_calls: int | None = None
    incidents_created: int | None = None


@dataclass(frozen=True)
class Requests:
    """버그가 든 shopapi에 순서대로 보내는 요청. 처음 난 예외가 장애다."""

    items: list[dict]


@dataclass(frozen=True)
class Startup:
    """환경변수를 지운 채 shopapi를 기동한다. 기동 중 예외가 장애다."""

    unset_env: list[str]


@dataclass(frozen=True)
class Alert:
    """스택트레이스 없이 모니터링 알림 문구로만 들어오는 장애."""

    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class Recurrence:
    """base 시나리오를 처리한 뒤, 배포 전에 같은 장애가 다시 들어온다."""

    base: str


@dataclass(frozen=True)
class Duplicate:
    """base 시나리오의 같은 알림이 짧은 시간에 여러 번 들어온다."""

    base: str
    times: int


Trigger = Requests | Startup | Alert | Recurrence | Duplicate

TRIGGERS = {
    "requests": Requests,
    "startup": lambda v: Startup(**v),
    "alert": lambda v: Alert(**v),
    "recurrence": lambda v: Recurrence(**v),
    "duplicate": lambda v: Duplicate(**v),
}


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    type: str
    difficulty: str
    expected: Expected
    trigger: Trigger
    hidden_test: Path | None


def load_scenario(scenario_id: str) -> Scenario:
    raw = yaml.safe_load((SCENARIOS / f"{scenario_id}.yaml").read_text(encoding="utf-8"))
    kinds = [k for k in TRIGGERS if k in raw]
    if len(kinds) != 1:
        raise ValueError(f"{scenario_id}: {sorted(TRIGGERS)} 중 정확히 하나가 있어야 한다. 지금은 {kinds}")
    return Scenario(
        id=raw["id"],
        title=raw["title"],
        type=raw["type"],
        difficulty=raw["difficulty"],
        expected=Expected(**raw["expected"]),
        trigger=TRIGGERS[kinds[0]](raw[kinds[0]]),
        hidden_test=SCENARIOS / raw["hidden_test"] if "hidden_test" in raw else None,
    )


def inject(scenario_id: str, repo: Path, revert: bool = False) -> bool:
    """버그 패치가 있으면 repo에 적용(또는 원복)하고 True를 돌려준다. 코드 버그가 아닌 시나리오는 패치가 없다."""
    patch = SCENARIOS / f"{scenario_id}.patch"
    if not patch.exists():
        return False
    cmd = ["git", "-C", str(repo), "apply"]
    top = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode == 0:
        # git 저장소 안에서는 "diff --git" 패치 경로를 저장소 루트 기준으로 읽고, 작업 폴더 밖이면 오류 없이 건너뛴다
        root = Path(top.stdout.strip())
        cmd = ["git", "-C", str(root), "apply", f"--directory={repo.resolve().relative_to(root.resolve())}"]
    subprocess.run([*cmd, *(["-R"] if revert else []), str(patch)], check=True)
    return True


@contextmanager
def injected(scenario_id: str, repo: Path) -> Iterator[None]:
    applied = inject(scenario_id, repo)
    try:
        yield
    finally:
        if applied:
            inject(scenario_id, repo, revert=True)


def _load_shopapi(repo: Path):
    """시나리오마다 다른 shopapi 복사본을 쓰므로 이전에 import한 모듈을 지우고 새로 읽는다."""
    for name in [m for m in sys.modules if m == "shopapi" or m.startswith("shopapi.")]:
        del sys.modules[name]
    sys.path.insert(0, str(repo))
    try:
        from shopapi.config import Settings
        from shopapi.main import create_app
    finally:
        sys.path.remove(str(repo))
    return Settings, create_app


def _incident(exc: Exception, where: str) -> dict:
    return {
        "message": f"{type(exc).__name__}: {exc} on {where}",
        "stacktrace": "".join(traceback.format_exception(exc)),
        "severity": "critical",
    }


def _reproduce_requests(s: Scenario, t: Requests, repo: Path) -> dict:
    from fastapi.testclient import TestClient

    Settings, create_app = _load_shopapi(repo)
    settings = Settings(pg_url="http://fake-pg", pg_api_key="k", pg_timeout_sec=1, tax_rate=0.1, page_size=5, slack_webhook_url="")
    client = TestClient(create_app(settings), raise_server_exceptions=True)
    for req in t.items:
        try:
            r = client.request(req["method"], req["path"], json=req.get("json"), params=req.get("params"))
        except Exception as exc:  # noqa: BLE001 - 어떤 예외든 장애 알림으로 만든다
            return _incident(exc, f"{req['method']} {req['path']}")
    raise RuntimeError(f"{s.id}: 마지막 요청이 {r.status_code}로 끝나 장애가 재현되지 않았다. 버그가 주입됐는지 확인한다")


def _reproduce_startup(s: Scenario, t: Startup, repo: Path) -> dict:
    _, create_app = _load_shopapi(repo)
    saved = {k: os.environ.pop(k) for k in t.unset_env if k in os.environ}
    try:
        create_app()
    except Exception as exc:  # noqa: BLE001 - 기동 실패 전체를 장애 알림으로 만든다
        return _incident(exc, "startup")
    finally:
        os.environ.update(saved)
    raise RuntimeError(f"{s.id}: {t.unset_env}를 지워도 shopapi가 정상 기동했다")


def reproduce(s: Scenario, repo: Path | None = None) -> dict:
    """버그가 들어간 shopapi로 장애를 일으키고, 운영 알림과 같은 형태의 장애 정보를 만든다."""
    repo = repo or SHOPAPI
    match s.trigger:
        case Requests() as t:
            return _reproduce_requests(s, t, repo)
        case Startup() as t:
            return _reproduce_startup(s, t, repo)
        case Alert() as t:
            return {"message": t.message, "severity": t.severity}
        case Recurrence(base=base) | Duplicate(base=base):
            return reproduce(load_scenario(base), repo)


def submit(api: httpx.Client, incident: dict) -> dict:
    body = {"source": "experiment", "channel": os.environ.get("SLACK_CHANNEL", "C-EXPERIMENT"), "service": "shopapi", **incident}
    r = api.post("/api/v1/incidents", json=body)
    r.raise_for_status()
    return r.json()


def wait(api: httpx.Client, incident_id: str, timeout_sec: float = 600, interval_sec: float = 3) -> dict:
    deadline = time.monotonic() + timeout_sec
    while True:
        status = api.get(f"/api/v1/incidents/{incident_id}").json()
        if status.get("status") in FINAL_STATUSES:
            return status
        if time.monotonic() > deadline:
            return {**status, "status": "timeout"}
        time.sleep(interval_sec)


def judge(s: Scenario, patch: str) -> dict:
    """버그가 들어간 현재 shopapi에 AI 패치를 적용하고 숨은 테스트만, 전체(숨은 테스트 포함) 순서로 돌린다."""
    if not patch or s.hidden_test is None:
        return {"hidden_passed": None, "full_passed": None}
    tmp = Path(tempfile.mkdtemp(prefix="judge-"))
    try:
        repo = tmp / "shopapi"
        shutil.copytree(SHOPAPI, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        hidden = f"tests/test_hidden_{s.id.lower()}.py"
        shutil.copy(s.hidden_test, repo / hidden)
        runner = SandboxTestRunner(str(repo))
        hidden_run = runner.run(patch, [hidden])
        full_run = runner.run(patch, None, full_suite=True)
        return {"hidden_passed": hidden_run.passed, "full_passed": full_run.passed,
                "hidden_summary": hidden_run.summary, "full_summary": full_run.summary}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CACHE_PATTERNS = ("llm:*", "dedupe:*", "rag:q:*")
CONDITIONS = {"A": True, "B": False}
COLUMNS = [
    "condition", "run", "started_at", "scenario", "type", "difficulty", "incident_id",
    "outcome", "expected_outcome", "outcome_correct", "category", "expected_category", "category_correct",
    "attempts", "elapsed_sec", "usd", "tokens_in", "tokens_out", "llm_calls", "calls_by_model", "cache_hits",
    "reused_fix", "tests_selected", "test_files_total", "test_sec",
    "hidden_passed", "full_passed", "false_pass", "incidents_created", "procedure_usd",
]


def reset_caches(redis: Redis, db: ConnectionPool) -> None:
    """임베딩 캐시는 LLM 비용과 무관한 로컬 계산이라 남긴다.
    검증된 수정안은 지우지 않고 배포된 것으로 표시해 재사용 대상에서만 뺀다(이력은 남는다)."""
    for pattern in CACHE_PATTERNS:
        for key in redis.scan_iter(pattern):
            redis.delete(key)
    FixStore(db).retire_all()


def verify_condition(api: httpx.Client, condition: str) -> dict:
    config = api.get("/api/v1/config").json()
    want = CONDITIONS[condition]
    off_target = {k: v for k, v in config["optimizations"].items() if v != want}
    if off_target:
        raise SystemExit(f"서버 설정이 조건 {condition}와 다르다: {off_target}. .env의 OPT_*를 고치고 api, worker를 다시 만든다")
    return config


@dataclass
class Backend:
    api: httpx.Client
    redis: Redis
    db: ConnectionPool
    process: Callable[[], None] = lambda: None  # 실제 실행에서는 워커 컨테이너가 처리한다
    timeout_sec: float = 600
    interval_sec: float = 3


@dataclass
class Run:
    accepted: dict
    status: dict
    elapsed_sec: float
    verdict: dict


def _run(s: Scenario, b: Backend) -> Run:
    with injected(s.id, SHOPAPI):
        started = time.monotonic()
        accepted = submit(b.api, reproduce(s))
        b.process()
        status = wait(b.api, accepted["incident_id"], b.timeout_sec, b.interval_sec)
        elapsed = time.monotonic() - started
        verdict = judge(s, status.get("patch", ""))
    return Run(accepted, status, elapsed, verdict)


def _run_duplicate(base: Scenario, times: int, b: Backend) -> tuple[Run, int]:
    with injected(base.id, SHOPAPI):
        started = time.monotonic()
        incident = reproduce(base)
        accepted = [submit(b.api, incident) for _ in range(times)]
        b.process()
        status = wait(b.api, accepted[0]["incident_id"], b.timeout_sec, b.interval_sec)
        elapsed = time.monotonic() - started
        verdict = judge(base, status.get("patch", ""))
    return Run(accepted[0], status, elapsed, verdict), len({a["incident_id"] for a in accepted})


def run_scenario(s: Scenario, b: Backend) -> dict:
    reset_caches(b.redis, b.db)
    match s.trigger:
        case Recurrence(base=base):
            base_s = load_scenario(base)
            first = _run(base_s, b)
            # 중복 제거 기간이 지났다고 보고 같은 장애를 다시 접수한다. 캐시와 검증된 수정안은 남긴다
            b.redis.delete(f"dedupe:{first.accepted['fingerprint']}")
            row = summarize(s, _run(base_s, b))
            # 행의 지표는 재접수 건이지만, 실제로 쓴 비용에는 base 처리 비용도 들어간다
            row["procedure_usd"] = round(row["usd"] + _usd(first.status), 6)
            return row
        case Duplicate(base=base, times=times):
            run, created = _run_duplicate(load_scenario(base), times, b)
            return summarize(s, run, incidents_created=created)
        case _:
            return summarize(s, _run(s, b))


def _usd(status: dict) -> float:
    return float((status.get("cost") or {}).get("usd", 0))


def summarize(s: Scenario, run: Run, incidents_created: int | None = None) -> dict:
    status, verdict = run.status, run.verdict
    cls = status.get("classification") or {}
    cost = status.get("cost") or {}
    calls = {k.removeprefix("calls:"): v for k, v in cost.items() if k.startswith("calls:")}
    selection = status.get("test_selection") or {}
    result = status.get("test_result") or {}
    outcome = status.get("status")
    category = cls.get("category")
    return {
        "scenario": s.id,
        "type": s.type,
        "difficulty": s.difficulty,
        "incident_id": run.accepted["incident_id"],
        "outcome": outcome,
        "expected_outcome": s.expected.outcome,
        "outcome_correct": outcome == s.expected.outcome,
        "category": category,
        "expected_category": s.expected.category,
        "category_correct": category == s.expected.category if s.expected.category else None,
        "attempts": status.get("attempts", 0),
        "elapsed_sec": round(run.elapsed_sec, 1),
        "usd": cost.get("usd", 0),
        "tokens_in": cost.get("tokens_in", 0),
        "tokens_out": cost.get("tokens_out", 0),
        "llm_calls": sum(calls.values()),
        "calls_by_model": json.dumps(calls, sort_keys=True),
        "cache_hits": sum(v for k, v in cost.items() if k.startswith("cache_hit:")),
        "reused_fix": bool(status.get("reused_fix")),
        "tests_selected": ("full" if selection.get("full_suite") else len(selection.get("tests", []))) if selection else None,
        "test_files_total": selection.get("total_test_files"),
        "test_sec": result.get("duration_sec"),
        "hidden_passed": verdict.get("hidden_passed"),
        "full_passed": verdict.get("full_passed"),
        "false_pass": outcome == "resolved" and verdict.get("hidden_passed") is False,
        "incidents_created": incidents_created,
        "procedure_usd": cost.get("usd", 0),
    }


def append_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k) for k in COLUMNS})


def run_all(scenarios: list[Scenario], b: Backend, condition: str, repeat: int, out: Path | None,
            max_usd: float | None = None) -> float:
    """조건과 반복 횟수만큼 실행하고 누적 LLM 비용을 돌려준다. 누적 비용이 max_usd에 닿으면 멈춘다."""
    spent = 0.0
    for n in range(1, repeat + 1):
        for s in scenarios:
            if max_usd is not None and spent >= max_usd:
                print(f"누적 비용 ${spent:.4f}가 상한 ${max_usd:.2f}에 닿아 멈춘다", file=sys.stderr, flush=True)
                return spent
            started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            row = {"condition": condition, "run": n, "started_at": started_at, **run_scenario(s, b)}
            spent += float(row["procedure_usd"] or 0)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if out:
                append_csv(out, row)
    return spent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--condition", choices=sorted(CONDITIONS), required=True)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", type=Path, help="결과를 붙일 CSV. 없으면 JSON 줄로만 출력")
    ap.add_argument("--api", default=os.environ.get("AUTOMATION_API", "http://localhost:8000"))
    ap.add_argument("--redis", default=os.environ.get("EXPERIMENT_REDIS_URL", "redis://localhost:6379/0"))
    ap.add_argument("--database", default=os.environ.get("EXPERIMENT_DATABASE_URL", "postgresql://aiops:aiops@localhost:5432/aiops"))
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--max-usd", type=float, help="누적 LLM 비용이 이 금액에 닿으면 남은 실행을 멈춘다")
    args = ap.parse_args()

    scenarios = [load_scenario(sid) for sid in args.scenarios]
    api = httpx.Client(base_url=args.api, headers={"X-Automation-Token": os.environ["AUTOMATION_TOKEN"]}, timeout=30)
    verify_condition(api, args.condition)
    backend = Backend(api, make_redis(args.redis), make_pool(args.database), timeout_sec=args.timeout)

    spent = run_all(scenarios, backend, args.condition, args.repeat, args.out, args.max_usd)
    print(f"누적 LLM 비용 ${spent:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
