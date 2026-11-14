"""Slack 실연동 경로를 끝까지 확인한다.

실행: python -m scripts.slack_e2e S01 S02 S05 --out results/day4_slack.csv

시나리오마다 shopapi에 버그를 주입하고, 시나리오 요청을 실제 shopapi(localhost:8100)로 보낸다.
shopapi가 Slack에 올린 장애 알림의 스레드에 자동화 회신이 달릴 때까지 기다린 뒤 원복한다.
워커는 수정안을 검증할 때 /repo(같은 shopapi 폴더)를 복사하므로 회신 전에는 원복하지 않는다.
"""
import argparse
import csv
import re
import time
from pathlib import Path

import httpx

from scripts.run_experiment import ROOT, SHOPAPI, Requests, injected, load_scenario

SHOPAPI_URL = "http://localhost:8100"
COLUMNS = ["scenario", "alert_ts", "reply_ts", "alert_to_reply_sec", "alert_chars", "outcome_title", "usd"]


def read_env() -> dict[str, str]:
    return dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))


class Slack:
    def __init__(self, token: str, channel: str):
        self.http = httpx.Client(base_url="https://slack.com/api", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        self.channel = channel

    def first_alert_since(self, oldest: float) -> dict | None:
        msgs = self.http.get("/conversations.history", params={"channel": self.channel, "oldest": f"{oldest:.6f}"}).json()["messages"]
        alerts = [m for m in msgs if m.get("text", "").startswith("[shopapi]")]
        return min(alerts, key=lambda m: float(m["ts"])) if alerts else None

    def first_reply(self, ts: str) -> dict | None:
        msgs = self.http.get("/conversations.replies", params={"channel": self.channel, "ts": ts}).json().get("messages", [])
        return msgs[1] if len(msgs) > 1 else None


def wait_for(fn, timeout_sec: float, interval_sec: float = 3):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        found = fn()
        if found:
            return found
        time.sleep(interval_sec)
    return None


def run(sid: str, slack: Slack, timeout_sec: float) -> dict:
    s = load_scenario(sid)
    if not isinstance(s.trigger, Requests):
        raise SystemExit(f"{sid}: Slack 경로는 shopapi 요청으로 재현하는 시나리오만 쓴다")
    with injected(sid, SHOPAPI):
        time.sleep(4)  # shopapi --reload가 바뀐 코드를 읽을 시간
        started = time.time()
        for req in s.trigger.items:
            httpx.request(req["method"], SHOPAPI_URL + req["path"], json=req.get("json"), params=req.get("params"), timeout=10)
        alert = wait_for(lambda: slack.first_alert_since(started - 1), 60)
        if alert is None:
            raise SystemExit(f"{sid}: 60초 안에 #alert에 장애 알림이 올라오지 않았다")
        reply = wait_for(lambda: slack.first_reply(alert["ts"]), timeout_sec)
    if reply is None:
        raise SystemExit(f"{sid}: {timeout_sec}초 안에 스레드 회신이 없다")
    text = reply.get("text", "")
    cost = re.search(r"LLM 비용\*: \$([\d.]+)", text)
    return {
        "scenario": sid,
        "alert_ts": alert["ts"],
        "reply_ts": reply["ts"],
        "alert_to_reply_sec": round(float(reply["ts"]) - float(alert["ts"]), 1),
        "alert_chars": len(alert["text"]),
        "outcome_title": text.splitlines()[0] if text else "",
        "usd": float(cost.group(1)) if cost else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args()
    env = read_env()
    slack = Slack(env["SLACK_BOT_TOKEN"], env["SLACK_CHANNEL_ID"])
    rows = []
    for sid in args.scenarios:
        row = run(sid, slack, args.timeout)
        print(row, flush=True)
        rows.append(row)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
