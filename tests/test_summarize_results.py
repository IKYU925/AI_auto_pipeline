from scripts.run_experiment import COLUMNS
from scripts.summarize_results import summarize


def row(cond, run, scenario="S01", type_="code_bug", usd="0.1", hidden="True", full="True", false_pass="False",
        cat="True", attempts="1", elapsed="30", llm_calls="3"):
    base = dict.fromkeys(COLUMNS, "")
    base.update(condition=cond, run=str(run), scenario=scenario, type=type_, usd=usd, hidden_passed=hidden,
                full_passed=full, false_pass=false_pass, category_correct=cat, attempts=attempts,
                elapsed_sec=elapsed, llm_calls=llm_calls)
    return base


def line(table, metric):
    return next(l for l in table.splitlines() if l.startswith(f"| {metric}"))


def test_mean_and_range_per_condition():
    rows = [row("A", 1, usd="0.10"), row("A", 2, usd="0.20"), row("B", 1, usd="0.50"), row("B", 2, usd="0.70")]
    l = line(summarize(rows), "장애당 평균 비용")
    assert "$0.1500 ($0.1000~$0.2000)" in l and "$0.6000 ($0.5000~$0.7000)" in l and l.endswith("차이 있음 |")


def test_overlapping_ranges_mean_no_difference():
    rows = [row("A", 1, elapsed="10"), row("A", 2, elapsed="40"), row("B", 1, elapsed="30"), row("B", 2, elapsed="50")]
    assert line(summarize(rows), "처리 시간 중앙값").endswith("차이 없음(범위 겹침) |")


def test_fix_success_counts_only_code_bugs_and_false_pass():
    rows = [row("A", 1, hidden="True"), row("A", 1, scenario="S02", hidden="False", false_pass="True"),
            row("A", 1, scenario="S10", type_="infra", hidden="", full="")]
    table = summarize(rows)
    assert "50% (50%~50%)" in line(table, "자동 수정 성공률")
    assert "1 (1~1)" in line(table, "거짓 통과")


def test_single_run_withholds_verdict():
    rows = [row("A", 1, usd="0.10"), row("B", 1, usd="0.50")]
    assert line(summarize(rows), "장애당 평균 비용").endswith("판단 보류(1회 실행) |")


def test_label_groups_same_condition_from_different_phases(tmp_path, monkeypatch, capsys):
    import csv
    import sys

    from scripts.summarize_results import main

    paths = []
    for name, usd in (("p1", "0.10"), ("p2", "0.30")):
        p = tmp_path / f"{name}.csv"
        with p.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerow(row("A", 1, usd=usd))
        paths.append(p)
    monkeypatch.setattr(sys, "argv", ["x", f"1단계={paths[0]}", f"2단계={paths[1]}"])
    main()
    l = line(capsys.readouterr().out, "장애당 평균 비용")
    assert "$0.1000" in l and "$0.3000" in l
