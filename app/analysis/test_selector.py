"""영향도 분석 결과로 이번 변경과 관련된 테스트만 고른다.

전체 테스트를 매번 돌리면 재시도 루프 1회마다 시간이 오래 걸리고 CI 비용도 커진다.
다만 판단 근거가 부족한 경우(코드 외 파일 변경, conftest 변경, 선택된 테스트 0개)에는
안전하게 전체 테스트로 대체한다.
"""
from dataclasses import asdict, dataclass
from pathlib import Path

from app.analysis.impact import ImpactReport, ImportGraph


@dataclass
class TestSelection:
    __test__ = False  # pytest가 테스트 클래스로 수집하지 않도록

    tests: list[str]         # 저장소 기준 테스트 파일 경로. full_suite면 빈 리스트
    full_suite: bool
    reason: str
    total_test_files: int

    def to_dict(self) -> dict:
        return asdict(self)


def select_tests(impact: ImpactReport, graph: ImportGraph, fallback_full_suite: bool = True) -> TestSelection:
    all_tests = graph.test_modules()
    total = len(all_tests)

    if impact.non_python_changes or any(m.endswith("conftest") for m in impact.changed_modules):
        return TestSelection([], True, "설정, conftest 등 코드 외 변경이 있어 전체 테스트 실행", total)

    selected: set[str] = set()
    # 1) 영향 범위 안에 들어온 테스트 모듈
    selected |= {m for m in impact.impacted if graph.modules[m].is_test}
    # 2) 패치가 테스트 파일 자체를 수정한 경우
    selected |= {m for m in impact.changed_modules if graph.modules[m].is_test}
    # 3) 이름 규칙: shop/pricing.py -> test_pricing.py
    changed_stems = {m.split(".")[-1] for m in impact.changed_modules}
    selected |= {t for t in all_tests if Path(graph.modules[t].path).stem.removeprefix("test_") in changed_stems}

    if not selected:
        if fallback_full_suite:
            return TestSelection([], True, "관련 테스트를 찾지 못해 전체 테스트로 대체", total)
        return TestSelection([], False, "관련 테스트 없음(검증 불가)", total)

    paths = sorted(graph.modules[m].path for m in selected)
    return TestSelection(paths, False, f"전체 {total}개 테스트 파일 중 {len(paths)}개 선별", total)
