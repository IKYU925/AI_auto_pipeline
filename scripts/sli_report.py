"""자동화 파이프라인 SLI를 지금 쌓인 데이터로 계산한다(docs/sli.md).

실행: .venv/bin/python -m scripts.sli_report --since 2026-11-14 --results results/phase3.csv

SLI는 모두 "좋은 이벤트 수 / 전체 이벤트 수"다. 목표치(SLO)는 4단계 AWS 운영 데이터로 정하므로 여기서는 값만 낸다.
운영 데이터(PostgreSQL, Prometheus)로 셀 수 없는 SLI는 실험 결과 CSV에서 센다. 정답(기대 결과)이 실험에만 있기 때문이다.
"""
import argparse
import csv
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from scripts.run_cluster import PROMETHEUS, psql

CODE_PATH_SQL = "needs_code_fix"
REPLY_SQL = "to_timestamp(report_ts::numeric) - coalesce(occurred_at, created_at)"
DONE = ("resolved", "escalated", "no_code_fix")


@dataclass(frozen=True)
class Value:
    good: int
    total: int
    detail: str = ""

    @property
    def ratio(self) -> str:
        return f"{self.good / self.total:.1%}" if self.total else "-"


@dataclass(frozen=True)
class Window:
    since: str
    results: list[Path]

    def incidents(self, where: str) -> list[dict]:
        return psql(f"""SELECT id, status, needs_code_fix, report_ts, extract(epoch FROM {REPLY_SQL}) AS reply_sec FROM incidents
                        WHERE source = 'alertmanager' AND created_at >= '{self.since}' AND {where}""")

    def result_rows(self) -> list[dict]:
        return [r for p in self.results for r in csv.DictReader(p.open(encoding="utf-8"))]


def delivery(w: Window) -> Value:
    """Alertmanager가 자동화로 보낸 웹훅 중 성공한 비율. 커버리지(발생한 알림 중 접수 비율)를 셀 수 있을 때까지의 근사값이다."""
    def total(metric: str) -> int:
        q = f'sum(increase({metric}{{integration="webhook"}}[30d]))'
        r = httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": q}, timeout=10).json()["data"]["result"]
        return round(float(r[0]["value"][1])) if r else 0

    sent, failed = total("alertmanager_notifications_total"), total("alertmanager_notifications_failed_total")
    return Value(sent - failed, sent, "Prometheus 보존 기간(최근 30일) 기준")


def reply_within(limit_sec: int, code_path: bool) -> Callable[[Window], Value]:
    def compute(w: Window) -> Value:
        rows = w.incidents(f"{'' if code_path else 'NOT '}{CODE_PATH_SQL} AND report_ts IS NOT NULL")
        secs = sorted(float(r["reply_sec"]) for r in rows)
        p50 = secs[len(secs) // 2] if secs else None
        detail = f"중앙값 {p50:.0f}초, 최대 {secs[-1]:.0f}초" if secs else ""
        return Value(sum(s <= limit_sec for s in secs), len(secs), detail)

    compute.__doc__ = f"{'코드 버그' if code_path else '설정·인프라 장애'} 중 알림 시작부터 {limit_sec // 60}분 안에 회신한 비율"
    return compute


def processed(w: Window) -> Value:
    """접수된 장애 중 끝까지 처리돼 회신까지 간 비율. failed, budget_exceeded, 회신 없음은 실패다."""
    rows = w.incidents("created_at < now() - interval '15 minutes'")
    good = sum(r["status"] in DONE and r["report_ts"] is not None for r in rows)
    return Value(good, len(rows), f"실패 상태 {len(rows) - good}건")


def correct(w: Window) -> Value:
    """처리 결과(자동 수정, 사람 확인, 런북 안내)가 기대와 같은 비율."""
    rows = [r for r in w.result_rows() if r["outcome_correct"]]
    return Value(sum(r["outcome_correct"] == "True" for r in rows), len(rows), "실험 결과 CSV(정답은 시나리오의 기대 결과)")


def safe_fixes(w: Window) -> Value:
    """자동 수정으로 끝난 장애 중 실제로 자동 수정이 맞았던 비율. 틀리면 잘못된 수정이 배포될 수 있다."""
    rows = [r for r in w.result_rows() if r["outcome"] == "resolved"]
    wrong = [r["scenario"] for r in rows if r["expected_outcome"] != "resolved"]
    return Value(len(rows) - len(wrong), len(rows), f"잘못된 자동 수정: {', '.join(wrong) or '없음'}")


SLIS: list[tuple[str, Callable[[Window], Value]]] = [
    ("웹훅 전달", delivery),
    ("회신 시간(코드 버그, 3분)", reply_within(180, code_path=True)),
    ("회신 시간(설정·인프라 장애, 6분)", reply_within(360, code_path=False)),
    ("처리 성공", processed),
    ("처리 결과 정확도", correct),
    ("자동 수정 정확도", safe_fixes),
]


def report(w: Window) -> str:
    lines = ["| SLI | 정의 | 좋은 이벤트 / 전체 | 값 | 비고 |", "| --- | --- | --- | --- | --- |"]
    for name, compute in SLIS:
        v = compute(w)
        lines.append(f"| {name} | {compute.__doc__.splitlines()[0]} | {v.good} / {v.total} | {v.ratio} | {v.detail} |")
    usd = psql(f"""SELECT count(DISTINCT i.id) AS n, coalesce(sum(c.usd), 0) AS usd FROM incidents i
                   LEFT JOIN llm_calls c ON c.incident_id = i.id
                   WHERE i.source = 'alertmanager' AND i.created_at >= '{w.since}'""")[0]
    per = float(usd["usd"]) / usd["n"] if usd["n"] else 0
    lines.append(f"\n가드레일(SLI 아님): 장애 {usd['n']}건, 장애당 LLM 비용 ${per:.4f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="이 시각 이후 접수된 장애(예: 2026-11-14)")
    ap.add_argument("--results", type=Path, nargs="+", default=[Path("results/phase3.csv")])
    args = ap.parse_args()
    print(report(Window(args.since, args.results)))


if __name__ == "__main__":
    main()
