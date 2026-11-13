"""시나리오가 제대로 만들어졌는지 검사한다.

실행: python -m scripts.check_scenarios [S01 S02 ...]

요청으로 재현하는 코드 버그 시나리오는 shopapi 복사본에서 다음을 확인한다.
- 원본에서 공개 테스트와 숨은 테스트가 통과한다
- 패치를 적용하면 공개 테스트는 그대로 통과하고 숨은 테스트만 실패한다(배포된 버그는 기존 테스트를 통과한 버그다)
- 패치를 적용하면 시나리오 요청이 처리되지 않은 예외로 끝난다

기동 실패 시나리오는 원본 그대로 기동이 실패하는지, 재발과 중복 시나리오는 base가 코드 버그 시나리오인지 확인한다.
"""
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.run_experiment import (
    SCENARIOS,
    SHOPAPI,
    Alert,
    Duplicate,
    Recurrence,
    Requests,
    Scenario,
    Startup,
    load_scenario,
    reproduce,
)


def _pytest(repo: Path, *args: str) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True).returncode == 0


def _check_code_bug(s: Scenario) -> list[str]:
    if s.hidden_test is None:
        return ["숨은 테스트가 없다"]
    tmp = Path(tempfile.mkdtemp(prefix="check-"))
    try:
        repo = tmp / "shopapi"
        shutil.copytree(SHOPAPI, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        hidden = f"tests/test_hidden_{s.id.lower()}.py"
        shutil.copy(s.hidden_test, repo / hidden)

        expectations = [
            ("원본 공개 테스트 통과", lambda: _pytest(repo, "--ignore", hidden)),
            ("원본 숨은 테스트 통과", lambda: _pytest(repo, hidden)),
        ]
        failures = [name for name, ok in expectations if not ok()]

        applied = subprocess.run(["git", "apply", str(SCENARIOS / f"{s.id}.patch")], cwd=repo, capture_output=True, text=True)
        if applied.returncode != 0:
            return failures + [f"패치 적용 실패: {applied.stderr.strip()}"]

        if not _pytest(repo, "--ignore", hidden):
            failures.append("주입 후 공개 테스트가 실패한다(공개 테스트가 버그를 잡는다)")
        if _pytest(repo, hidden):
            failures.append("주입 후 숨은 테스트가 통과한다(버그를 잡지 못한다)")
        try:
            reproduce(s, repo)
        except RuntimeError as e:
            failures.append(str(e))
        return failures
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_startup(s: Scenario) -> list[str]:
    try:
        reproduce(s)
    except RuntimeError as e:
        return [str(e)]
    return []


def _check_repeat(s: Scenario, base: str) -> list[str]:
    if not isinstance(load_scenario(base).trigger, Requests):
        return [f"base {base}가 요청으로 재현하는 코드 버그 시나리오가 아니다"]
    if isinstance(s.trigger, Duplicate) and s.trigger.times < 2:
        return ["중복 횟수는 2 이상이어야 한다"]
    return []


def check(s: Scenario) -> list[str]:
    match s.trigger:
        case Requests():
            return _check_code_bug(s)
        case Startup():
            return _check_startup(s)
        case Alert(message=message):
            return [] if message.strip() else ["알림 문구가 비어 있다"]
        case Recurrence(base=base) | Duplicate(base=base):
            return _check_repeat(s, base)


def main() -> None:
    logging.getLogger("shopapi").setLevel(logging.CRITICAL)
    ids = sys.argv[1:] or sorted(p.stem for p in SCENARIOS.glob("S*.yaml"))
    bad = 0
    for sid in ids:
        s = load_scenario(sid)
        failures = check(s)
        bad += bool(failures)
        print(f"{sid}  {'OK' if not failures else 'FAIL'}  {s.title}")
        for f in failures:
            print(f"      - {f}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
