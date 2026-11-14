"""shopapi가 클러스터 관측 도구와 맺는 계약: /metrics의 5xx 집계와 Loki가 읽는 JSON 로그."""
import io
import json
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from scripts.run_experiment import SHOPAPI

sys.path.insert(0, str(SHOPAPI))
from shopapi.config import Settings  # noqa: E402
from shopapi.main import create_app  # noqa: E402
from shopapi.observability import JsonFormatter  # noqa: E402


SETTINGS = Settings(pg_url="http://pg", pg_api_key="k", pg_timeout_sec=1, tax_rate=0.1, page_size=5, slack_webhook_url="")


@pytest.fixture
def app():
    app = create_app(SETTINGS)

    @app.get("/boom")
    def boom():
        raise ZeroDivisionError("float division by zero")

    return app


def _counter(metrics: str, status: str) -> float:
    return sum(float(line.rsplit(" ", 1)[1]) for line in metrics.splitlines()
               if line.startswith("http_requests_total{") and f'status="{status}"' in line)


def test_metrics_group_unhandled_errors_as_5xx(app):
    client = TestClient(app, raise_server_exceptions=False)
    before = _counter(client.get("/metrics").text, "5xx")
    assert client.get("/boom").status_code == 500
    client.get("/products")
    metrics = client.get("/metrics").text
    assert _counter(metrics, "5xx") == before + 1
    assert "http_request_duration_seconds_bucket" in metrics, "결제 지연 p95 알림이 쓰는 히스토그램"
    assert 'handler="/metrics"' not in metrics


def test_unhandled_error_is_one_json_line_with_traceback(app):
    [handler] = [h for h in logging.getLogger("shopapi").handlers if isinstance(h.formatter, JsonFormatter)]
    out = io.StringIO()
    previous = handler.setStream(out)
    try:
        TestClient(app, raise_server_exceptions=False).get("/boom")
    finally:
        handler.setStream(previous)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    [line] = [l for l in lines if l["level"] == "ERROR"]
    assert line["path"] == "/boom" and "GET /boom" in line["message"]
    assert line["exc_info"].startswith("Traceback") and line["exc_info"].rstrip().endswith("ZeroDivisionError: float division by zero")
    assert "service" not in line, "서비스 이름은 Alloy가 스트림 라벨로 붙인다"


def test_json_handler_is_attached_once(app):
    create_app(SETTINGS)
    handlers = [h for h in logging.getLogger("shopapi").handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(handlers) == 1
