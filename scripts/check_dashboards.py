"""deploy/k8s/base/dashboards의 모든 패널 질의를 Grafana API로 실행해 오류와 빈 결과를 찾는다.

    GRAFANA_PASSWORD=... .venv/bin/python scripts/check_dashboards.py [--from now-7d]

Grafana에 올라간 대시보드가 저장소의 JSON과 같은지도 확인한다. 하나라도 오류가 나면 종료 코드 1.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DASHBOARDS = Path(__file__).resolve().parents[1] / "deploy/k8s/base/dashboards"


def frames_rows(result: dict) -> int:
    return sum(len(f["data"]["values"][0]) if f["data"]["values"] else 0 for f in result.get("frames", []))


def all_values(dashboard: dict) -> dict[str, str]:
    """대시보드 변수를 "전체 선택" 값으로 바꾼다. /api/ds/query는 변수를 채워 주지 않는다."""
    out = {}
    for v in dashboard.get("templating", {}).get("list", []):
        # 질의 변수는 그 질의를 하위 질의로 넣고(SQL IN), 고정 목록 변수는 allValue(정규식)를 넣는다
        out[f"${v['name']}"] = v["query"] if v["type"] == "query" else v["allValue"]
    return out


def fill(target: dict, values: dict[str, str]) -> dict:
    text = json.dumps(target, ensure_ascii=False)
    for name, value in values.items():
        text = text.replace(name, json.dumps(value, ensure_ascii=False)[1:-1])
    return json.loads(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("GRAFANA_URL", "http://192.168.56.10:30030"))
    ap.add_argument("--user", default=os.environ.get("GRAFANA_USER", "admin"))
    ap.add_argument("--from", dest="start", default="now-7d")
    args = ap.parse_args()
    grafana = httpx.Client(base_url=args.url, auth=(args.user, os.environ["GRAFANA_PASSWORD"]), timeout=30)

    failed = 0
    for path in sorted(DASHBOARDS.glob("*.json")):
        local = json.loads(path.read_text())
        res = grafana.get(f"/api/dashboards/uid/{local['uid']}")
        deployed = res.status_code == 200 and res.json()["dashboard"]["panels"] == local["panels"]
        print(f"== {local['title']} ({path.name}) Grafana 반영: {'같음' if deployed else '다름 또는 없음'}")
        failed += not deployed
        panels = [q for p in local["panels"] for q in (p.get("panels", []) if p["type"] == "row" else [p])]
        values = all_values(local)
        for panel in panels:
            for target in panel["targets"]:
                body = {"queries": [{**fill(target, values), "intervalMs": 60000, "maxDataPoints": 500}],
                        "from": args.start, "to": "now"}
                result = grafana.post("/api/ds/query", json=body).json().get("results", {}).get(target["refId"], {})
                error = result.get("error") or ("" if "frames" in result else "응답에 결과 없음")
                rows = frames_rows(result) if not error else 0
                status = "오류" if error else ("비어 있음" if rows == 0 else "정상")
                failed += bool(error)
                print(f"  [{status}] {panel['title']}/{target['refId']} 행 {rows} {error[:200]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
