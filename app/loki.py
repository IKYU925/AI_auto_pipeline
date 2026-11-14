"""Loki에서 서비스의 최근 ERROR 로그(shopapi JSON 로그의 exc_info)를 꺼낸다."""
import json
import logging
from datetime import datetime, timedelta

import httpx

log = logging.getLogger(__name__)
LOOKBACK = timedelta(minutes=5)


class LokiClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=10, transport=transport)

    def latest_exception(self, namespace: str, service: str, since: datetime, until: datetime) -> str | None:
        query = f'{{namespace="{namespace}", service="{service}"}} | json | level="ERROR"'
        params = {"query": query, "start": int((since - LOOKBACK).timestamp() * 1e9),
                  "end": int(until.timestamp() * 1e9), "limit": 1, "direction": "backward"}
        try:
            resp = self.http.get("/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            streams = resp.json()["data"]["result"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            # 스택트레이스 없이도 접수는 한다. 분류와 런북 안내는 알림 문구만으로 할 수 있다
            log.warning("loki lookup failed for %s/%s: %s", namespace, service, e)
            return None
        lines = sorted((v for s in streams for v in s["values"]), key=lambda v: int(v[0]), reverse=True)
        for _ts, line in lines:
            try:
                if exc := json.loads(line).get("exc_info"):
                    return exc
            except ValueError:
                continue
        return None
