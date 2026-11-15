"""Loki에서 알림의 근거 로그를 꺼낸다. 코드 버그는 최근 ERROR 로그의 exc_info, Pod 알림은 그 Pod의 마지막 로그 줄."""
import json
import logging
from datetime import datetime, timedelta

import httpx

log = logging.getLogger(__name__)
LOOKBACK = timedelta(minutes=5)


class LokiClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=10, transport=transport)

    def _lines(self, query: str, since: datetime, until: datetime, limit: int) -> list[str] | None:
        """최신 줄부터. 조회에 실패해도 접수는 한다. 분류와 런북 안내는 알림 문구만으로 할 수 있다."""
        params = {"query": query, "start": int((since - LOOKBACK).timestamp() * 1e9),
                  "end": int(until.timestamp() * 1e9), "limit": limit, "direction": "backward"}
        try:
            resp = self.http.get("/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            streams = resp.json()["data"]["result"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("loki lookup failed for %s: %s", query, e)
            return None
        values = sorted((v for s in streams for v in s["values"]), key=lambda v: int(v[0]), reverse=True)
        return [line for _ts, line in values]

    def latest_exception(self, namespace: str, service: str, since: datetime, until: datetime) -> str | None:
        lines = self._lines(f'{{namespace="{namespace}", service="{service}"}} | json | level="ERROR"', since, until, 1)
        for line in lines or []:
            try:
                if exc := json.loads(line).get("exc_info"):
                    return exc
            except ValueError:
                continue
        return None

    def pod_tail(self, namespace: str, pod: str, since: datetime, until: datetime, limit: int = 40) -> str | None:
        """CrashLoop, OOMKilled 알림은 문구가 한 줄뿐이다. 기동 실패의 스택트레이스나 죽기 직전 로그가 분류 근거가 된다."""
        lines = self._lines(f'{{namespace="{namespace}", pod="{pod}"}}', since, until, limit)
        return "".join(line if line.endswith("\n") else line + "\n" for line in reversed(lines)) if lines else None
