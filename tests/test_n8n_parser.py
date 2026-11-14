"""n8n 워크플로의 Slack 알림 파서(JavaScript)를 node로 실행해 확인한다. node가 없으면 건너뛴다."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / "n8n" / "incident_workflow.json"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node가 없다")


def parse(events: list[dict]) -> list[dict]:
    code = next(n for n in json.loads(WORKFLOW.read_text())["nodes"] if n["name"] == "Parse alert")["parameters"]["jsCode"]
    script = (
        f"const $input = {{ all: () => {json.dumps([{'json': e} for e in events])} }};\n"
        f"const result = (() => {{\n{code}\n}})();\n"
        "console.log(JSON.stringify(result.map(i => i.json)));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


SHOPAPI_ALERT = {
    "type": "message", "subtype": "bot_message", "channel": "C01", "ts": "1763000000.000100",
    "text": "[shopapi] critical: ZeroDivisionError: float division by zero on POST /orders/summary\n```\n"
            "Traceback (most recent call last):\n  File \"/repo/shopapi/orders.py\", line 43, in average_unit_price\n"
            "    return round(subtotal(items) / quantity, 2)\nZeroDivisionError: float division by zero\n"
            "  File \"&lt;frozen runpy&gt;\", line 1, in &lt;module&gt;\n```",
}


def test_shopapi_alert_becomes_incident():
    [inc] = parse([SHOPAPI_ALERT])
    assert (inc["service"], inc["severity"], inc["channel"], inc["thread_ts"]) == ("shopapi", "critical", "C01", "1763000000.000100")
    assert inc["message"].startswith("ZeroDivisionError: float division by zero")
    assert 'File "/repo/shopapi/orders.py", line 43' in inc["stacktrace"]


def test_slack_html_entities_are_restored():
    [inc] = parse([SHOPAPI_ALERT])
    assert "<module>" in inc["stacktrace"] and "&lt;" not in inc["stacktrace"]


def test_thread_replies_and_chat_are_ignored():
    reply = {**SHOPAPI_ALERT, "ts": "1763000001.000200", "thread_ts": "1763000000.000100"}
    chat = {"type": "message", "channel": "C01", "ts": "1763000002.000300", "text": "점심 뭐 먹지"}
    assert parse([reply, chat]) == []
