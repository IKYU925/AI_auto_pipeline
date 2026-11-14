"""클러스터 수집용 계측. Prometheus는 /metrics를, Loki는 stdout의 JSON 로그를 읽는다.

로그 필드(level, message, path, exc_info)는 Loki 알림 규칙(level="ERROR")과
자동화가 스택트레이스를 꺼내는 조회(exc_info)가 그대로 쓰는 계약이다. 서비스 이름은
Alloy가 Pod 라벨에서 스트림 라벨 service로 붙이므로 넣지 않는다.
"""
import json
import logging
import sys

from fastapi import FastAPI
from prometheus_client import CollectorRegistry, ProcessCollector
from prometheus_fastapi_instrumentator import Instrumentator


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {"level": record.levelname, "message": record.getMessage()}
        if path := getattr(record, "path", None):
            line["path"] = path
        if record.exc_info:
            line["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def setup_logging(logger: logging.Logger) -> None:
    # create_app은 테스트에서 여러 번 불리므로 핸들러를 한 번만 단다
    if any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    # 레벨은 정하지 않는다. shopapi는 WARNING 이상만 남기고, 실험 스크립트가 로거를 끈 설정을 덮어쓰지 않기 위해서다
    logger.addHandler(handler)


def instrument(app: FastAPI) -> None:
    # 상태 코드를 2xx, 5xx로 묶어 5xx 비율 알림이 status="5xx" 하나로 계산되게 한다.
    # 레지스트리를 앱마다 둔다. 기본 레지스트리는 프로세스에 하나라, 한 프로세스에서 앱을 여러 번 만들면
    # (시나리오 검사, 테스트) 나중 앱의 요청이 /metrics에 잡히지 않는다
    registry = CollectorRegistry()
    ProcessCollector(registry=registry)
    Instrumentator(should_group_status_codes=True, excluded_handlers=["/metrics", "/healthz"], registry=registry
                   ).instrument(app).expose(app, include_in_schema=False)
