"""shopapi가 클러스터 관측 도구와 맺는 계약: /metrics의 5xx 집계와 Loki가 읽는 JSON 로그."""
import io
import json
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from scripts.run_experiment import SHOPAPI


@pytest.fixture
def shop(monkeypatch):
    """shopapi를 새로 불러오고 로거를 비운다. 실험 스크립트(_load_shopapi)가 같은 프로세스에서 모듈을 바꿔 끼우고
    로거를 CRITICAL로 꺼 두므로, 처음 import한 클래스와 로거 상태에 기대면 실행 순서에 따라 결과가 달라진다."""
    logger = logging.getLogger("shopapi")
    handlers, level = logger.handlers[:], logger.level
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    for name in [m for m in sys.modules if m == "shopapi" or m.startswith("shopapi.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(SHOPAPI))
    from shopapi import config, main, observability
    settings = config.Settings(pg_url="http://pg", pg_api_key="k", pg_timeout_sec=1, tax_rate=0.1, page_size=5,
                               slack_webhook_url="")
    yield main.create_app, settings, observability.JsonFormatter
    logger.handlers[:] = handlers
    logger.setLevel(level)


@pytest.fixture
def app(shop):
    create_app, settings, _ = shop
    app = create_app(settings)

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


def test_unhandled_error_is_one_json_line_with_traceback(app, shop):
    json_formatter = shop[2]
    [handler] = [h for h in logging.getLogger("shopapi").handlers if isinstance(h.formatter, json_formatter)]
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


def test_json_handler_is_attached_once(app, shop):
    create_app, settings, json_formatter = shop
    create_app(settings)
    handlers = [h for h in logging.getLogger("shopapi").handlers if isinstance(h.formatter, json_formatter)]
    assert len(handlers) == 1


def test_latency_histogram_can_express_slow_payments(app):
    """ShopapiSlowPayment는 handler별 p95 > 5초다. 1초가 끝인 버킷이면 p95가 1초를 넘지 못해 알림이 뜰 수 없다."""
    client = TestClient(app)
    client.get("/products")
    buckets = {line.split('le="')[1].split('"')[0] for line in client.get("/metrics").text.splitlines()
               if line.startswith("http_request_duration_seconds_bucket") and 'handler="/products"' in line}
    assert {"5.0", "10.0"} <= buckets, buckets
