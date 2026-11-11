"""격리된 작업 공간에서 패치를 적용하고 선별된 테스트를 실행한다.

원본 저장소는 절대 건드리지 않는다. 임시 디렉터리에 복사본을 만들고 git apply로 패치를 적용한 뒤
pytest를 실행한다. 실패 로그는 재시도 때 LLM에 다시 들어가므로 핵심 부분만 잘라서 토큰을 아낀다.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.analysis.diff_analyzer import split_patch_by_file
from app.analysis.impact import EXCLUDE_DIRS

_SUMMARY = re.compile(r"^=*\s*(\d+ (?:passed|failed|error).*? in [\d.]+s)", re.M)


@dataclass
class TestRunResult:
    __test__ = False

    applied: bool
    passed: bool
    exit_code: int | None
    duration_sec: float
    command: str
    summary: str
    failure_log: str

    def to_dict(self) -> dict:
        return asdict(self)


def trim_failure_log(output: str, max_chars: int) -> str:
    """pytest 출력에서 FAILURES 섹션과 short test summary만 남긴다."""
    start = output.find("= FAILURES =")
    if start == -1:
        start = output.find("= ERRORS =")
    core = output[start:] if start != -1 else output
    # 반복되는 site-packages 프레임은 원인 분석에 도움이 안 되므로 제거
    core = "\n".join(l for l in core.splitlines() if "site-packages" not in l)
    if len(core) <= max_chars:
        return core
    head = max_chars // 3
    return core[:head] + "\n...[로그 생략]...\n" + core[-(max_chars - head) :]


class SandboxTestRunner:
    def __init__(self, repo_root: str, timeout_sec: int = 300, max_log_chars: int = 4000, python: str = sys.executable):
        self.repo = Path(repo_root).resolve()
        self.timeout = timeout_sec
        self.max_log_chars = max_log_chars
        self.python = python

    def _copy_repo(self, dest: Path) -> None:
        shutil.copytree(self.repo, dest, ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc"))

    @staticmethod
    def _apply(tmp: Path, work: Path, patch: str) -> str | None:
        """파일 단위로 나눠 --recount로 적용한다. 하나라도 실패하면 에러 메시지를 돌려준다."""
        parts = split_patch_by_file(patch)
        if not parts:
            return "unified diff 형식의 파일 헤더(--- a/, +++ b/)를 찾지 못했다"
        for i, part in enumerate(parts):
            f = tmp / f"part{i}.patch"
            f.write_text(part, encoding="utf-8")
            proc = subprocess.run(
                ["git", "apply", "--recount", "--whitespace=nowarn", str(f)],
                cwd=work, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                return proc.stderr
        return None

    def run(self, patch: str, tests: list[str] | None, full_suite: bool = False) -> TestRunResult:
        tmp = Path(tempfile.mkdtemp(prefix="aiauto-"))
        work = tmp / "repo"
        started = time.monotonic()
        try:
            self._copy_repo(work)
            error = self._apply(tmp, work, patch)
            if error:
                return TestRunResult(
                    applied=False, passed=False, exit_code=1,
                    duration_sec=round(time.monotonic() - started, 2), command="git apply",
                    summary="패치 적용 실패",
                    failure_log=f"git apply 실패. 컨텍스트 줄이 원본 코드와 일치하지 않는다.\n{error[: self.max_log_chars]}",
                )

            cmd = [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-rfE"]
            if not full_suite and tests:
                cmd += tests
            try:
                proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired as e:
                return TestRunResult(
                    True, False, None, round(time.monotonic() - started, 2), " ".join(cmd[2:]),
                    f"테스트 시간 초과({self.timeout}s)", trim_failure_log(str(e.stdout or ""), self.max_log_chars),
                )
            output = proc.stdout + proc.stderr
            found = _SUMMARY.findall(output)
            summary = found[-1] if found else output.strip().splitlines()[-1] if output.strip() else ""
            # exit code 5 = 수집된 테스트 없음 -> 검증 실패로 본다
            passed = proc.returncode == 0
            return TestRunResult(
                applied=True,
                passed=passed,
                exit_code=proc.returncode,
                duration_sec=round(time.monotonic() - started, 2),
                command=" ".join(cmd[2:]),
                summary=summary,
                failure_log="" if passed else trim_failure_log(output, self.max_log_chars),
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
