"""1단계 A/B 실험 CSV를 plan.md 12장 결과 표로 요약한다.

실행: python -m scripts.summarize_results results/phase1_A.csv results/phase1_B.csv > results/phase1_summary.md
같은 조건을 단계끼리 비교할 때는 라벨=파일로 넘긴다. 그 파일의 행은 condition 대신 라벨로 묶인다.
     python -m scripts.summarize_results 1단계=results/phase1_A.csv 2단계=results/phase2_A.csv

지표마다 반복 실행(run)별 값을 먼저 구하고, 평균과 범위(최소~최대)를 적는다.
plan.md 7장 규칙대로 A와 B의 범위가 겹치면 "차이 없음"으로 본다.
"""
import csv
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

Row = dict[str, str]


def _bool(v: str) -> bool:
    return v == "True"


def _num(v: str) -> float | None:
    return float(v) if v not in ("", None) else None


def _ratio(rows: list[Row], ok: Callable[[Row], bool]) -> float | None:
    return sum(ok(r) for r in rows) / len(rows) if rows else None


def _median(values: list[float | None]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _mean(values: list[float | None]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def code_bugs(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r["type"] == "code_bug"]


METRICS: list[tuple[str, Callable[[list[Row]], float | None], str]] = [
    ("분류 정확도", lambda rs: _ratio([r for r in rs if r["category_correct"] in ("True", "False")], lambda r: _bool(r["category_correct"])), "pct"),
    ("자동 수정 성공률(숨은+전체 테스트 통과)", lambda rs: _ratio(code_bugs(rs), lambda r: _bool(r["hidden_passed"]) and _bool(r["full_passed"])), "pct"),
    ("거짓 통과(건)", lambda rs: float(sum(_bool(r["false_pass"]) for r in rs)), "int"),
    ("평균 시도 횟수(코드 버그)", lambda rs: _mean([_num(r["attempts"]) for r in code_bugs(rs)]), "f1"),
    ("처리 시간 중앙값(초)", lambda rs: _median([_num(r["elapsed_sec"]) for r in rs]), "f1"),
    ("장애당 평균 비용(USD)", lambda rs: _mean([_num(r["usd"]) for r in rs]), "usd"),
    ("테스트 실행 시간 중앙값(초)", lambda rs: _median([_num(r["test_sec"]) for r in rs]), "f2"),
    ("재발 시 LLM 호출 수(S12)", lambda rs: _mean([_num(r["llm_calls"]) for r in rs if r["scenario"] == "S12"]), "f1"),
]


def _fmt(v: float | None, kind: str) -> str:
    if v is None:
        return "-"
    return {"pct": f"{v * 100:.0f}%", "int": f"{v:.0f}", "f1": f"{v:.1f}", "f2": f"{v:.2f}", "usd": f"${v:.4f}"}[kind]


def per_run(rows: list[Row]) -> dict[str, list[list[Row]]]:
    """조건별로 반복 실행 단위의 행 묶음을 만든다."""
    grouped: dict[str, dict[str, list[Row]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        grouped[r["condition"]][r["run"]].append(r)
    return {cond: [runs[k] for k in sorted(runs, key=int)] for cond, runs in grouped.items()}


def summarize(rows: list[Row]) -> str:
    runs = per_run(rows)
    conds = sorted(runs)
    lines = ["| 지표 | " + " | ".join(f"{'조건 ' if len(c) == 1 else ''}{c} 평균 (범위)" for c in conds) + " | 판단 |", "| --- |" + " --- |" * (len(conds) + 1)]
    for name, fn, kind in METRICS:
        cells, ranges = [], []
        for c in conds:
            vals = [v for v in (fn(rs) for rs in runs[c]) if v is not None]
            if not vals:
                cells.append("-")
                ranges.append(None)
                continue
            lo, hi = min(vals), max(vals)
            cells.append(f"{_fmt(statistics.mean(vals), kind)} ({_fmt(lo, kind)}~{_fmt(hi, kind)})")
            ranges.append((lo, hi))
        verdict = "-"
        if len(ranges) == 2 and None not in ranges and min(len(runs[c]) for c in conds) < 2:
            verdict = "판단 보류(1회 실행)"
        elif len(ranges) == 2 and None not in ranges:
            (alo, ahi), (blo, bhi) = ranges
            verdict = "차이 없음(범위 겹침)" if alo <= bhi and blo <= ahi else "차이 있음"
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {verdict} |")
    counts = ", ".join(f"{'조건 ' if len(c) == 1 else ''}{c} {len(runs[c])}회 반복, {sum(len(rs) for rs in runs[c])}건" for c in conds)
    return f"실행 수: {counts}\n\n" + "\n".join(lines) + "\n"


def main() -> None:
    rows: list[Row] = []
    for arg in sys.argv[1:]:
        label, _, path = arg.rpartition("=")
        with Path(path).open(encoding="utf-8") as f:
            rows += [{**r, "condition": label} if label else r for r in csv.DictReader(f)]
    print(summarize(rows), end="")


if __name__ == "__main__":
    main()
