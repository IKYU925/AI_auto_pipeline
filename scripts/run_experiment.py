"""장애 시나리오 하나를 실행하고 결과를 판정한다.

실행: python -m scripts.run_experiment S01 [--api http://localhost:8000]

1. 시나리오 버그를 shopapi에 주입한다(워커의 /repo와 같은 폴더)
2. 시나리오 요청을 shopapi에 보내 실제 예외와 스택트레이스를 얻는다
3. 자동화 API로 장애를 접수하고 완료될 때까지 기다린다
4. AI 패치를 버그가 들어간 복사본에 적용하고 숨은 테스트와 전체 테스트로 판정한다
5. 버그를 원복하고 결과를 JSON 한 줄로 출력한다
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from app.testing.runner import SandboxTestRunner

ROOT = Path(__file__).resolve().parents[1]
SHOPAPI = ROOT / "targets" / "shopapi"
SCENARIOS = ROOT / "scenarios"
FINAL_STATUSES = {"resolved", "escalated", "no_code_fix", "budget_exceeded", "failed"}


@dataclass(frozen=True)
class Expected:
    category: str
    needs_code_fix: bool
    root_cause_file: str
    outcome: str


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    type: str
    difficulty: str
    expected: Expected
    requests: list[dict]
    hidden_test: Path


def load_scenario(scenario_id: str) -> Scenario:
    raw = yaml.safe_load((SCENARIOS / f"{scenario_id}.yaml").read_text(encoding="utf-8"))
    return Scenario(
        id=raw["id"],
        title=raw["title"],
        type=raw["type"],
        difficulty=raw["difficulty"],
        expected=Expected(**raw["expected"]),
        requests=raw["requests"],
        hidden_test=SCENARIOS / raw["hidden_test"],
    )


def inject(scenario_id: str, revert: bool = False) -> None:
    subprocess.run([str(ROOT / "scripts" / "inject.sh"), scenario_id, *(["revert"] if revert else [])], check=True)


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


def reproduce(s: Scenario, repo: Path | None = None) -> dict:
    """버그가 들어간 shopapi를 프로세스 안에서 띄워 요청을 보내고, 운영 알림과 같은 형태의 장애 정보를 만든다."""
    from fastapi.testclient import TestClient

    Settings, create_app = _load_shopapi(repo or SHOPAPI)
    settings = Settings(pg_url="http://fake-pg", pg_api_key="k", pg_timeout_sec=1, tax_rate=0.1, page_size=5, slack_webhook_url="")
    client = TestClient(create_app(settings), raise_server_exceptions=True)
    for req in s.requests:
        try:
            r = client.request(req["method"], req["path"], json=req.get("json"), params=req.get("params"))
        except Exception as exc:  # noqa: BLE001 - 어떤 예외든 장애 알림으로 만든다
            return {
                "message": f"{type(exc).__name__}: {exc} on {req['method']} {req['path']}",
                "stacktrace": "".join(traceback.format_exception(exc)),
            }
    raise RuntimeError(f"{s.id}: 마지막 요청이 {r.status_code}로 끝나 장애가 재현되지 않았다. 버그가 주입됐는지 확인한다")


def submit(api: httpx.Client, incident: dict) -> dict:
    body = {"source": "experiment", "channel": os.environ.get("SLACK_CHANNEL", "C-EXPERIMENT"), "service": "shopapi",
            "severity": "critical", **incident}
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
    if not patch:
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


def summarize(s: Scenario, accepted: dict, status: dict, verdict: dict, elapsed_sec: float) -> dict:
    cls = status.get("classification") or {}
    cost = status.get("cost") or {}
    outcome = status.get("status")
    return {
        "scenario": s.id,
        "incident_id": accepted["incident_id"],
        "outcome": outcome,
        "expected_outcome": s.expected.outcome,
        "category": cls.get("category"),
        "category_correct": cls.get("category") == s.expected.category,
        "attempts": status.get("attempts", 0),
        "elapsed_sec": round(elapsed_sec, 1),
        "usd": cost.get("usd", 0),
        "tokens_in": cost.get("tokens_in", 0),
        "tokens_out": cost.get("tokens_out", 0),
        **verdict,
        "false_pass": outcome == "resolved" and verdict.get("hidden_passed") is False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--api", default=os.environ.get("AUTOMATION_API", "http://localhost:8000"))
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()

    s = load_scenario(args.scenario)
    token = os.environ["AUTOMATION_TOKEN"]
    api = httpx.Client(base_url=args.api, headers={"X-Automation-Token": token}, timeout=30)

    inject(s.id)
    try:
        started = time.monotonic()
        accepted = submit(api, reproduce(s))
        status = wait(api, accepted["incident_id"], args.timeout)
        elapsed = time.monotonic() - started
        verdict = judge(s, status.get("patch", ""))
    finally:
        inject(s.id, revert=True)
    print(json.dumps(summarize(s, accepted, status, verdict, elapsed), ensure_ascii=False))


if __name__ == "__main__":
    main()
