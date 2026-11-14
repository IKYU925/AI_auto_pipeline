"""Redis Streams 기반 메시지 큐.

API는 요청을 큐에 넣고 바로 202를 돌려준다. LLM 호출과 테스트 실행은 수 분이 걸릴 수 있어서
HTTP 요청 안에서 처리하면 n8n 타임아웃, 재시도 폭주가 생기기 때문이다.
Consumer Group을 쓰면 워커를 여러 개 띄워도 메시지가 한 워커에만 전달되고,
처리 도중 워커가 죽으면 pending 메시지를 다른 워커가 다시 가져갈 수 있다(at-least-once).
"""
import json
import logging
from collections.abc import Iterator

from redis import Redis
from redis.exceptions import ResponseError

log = logging.getLogger(__name__)


class IncidentQueue:
    def __init__(self, redis: Redis, stream: str, group: str, dlq_stream: str):
        self.redis = redis
        self.stream = stream
        self.group = group
        self.dlq_stream = dlq_stream

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def enqueue(self, payload: dict) -> str:
        msg_id = self.redis.xadd(self.stream, {"data": json.dumps(payload, default=str)})
        return msg_id.decode() if isinstance(msg_id, bytes) else msg_id

    def consume(self, consumer: str, count: int = 1, block_ms: int = 5000) -> Iterator[tuple[str, dict]]:
        self.ensure_group()
        resp = self.redis.xreadgroup(self.group, consumer, {self.stream: ">"}, count=count, block=block_ms)
        for _stream, messages in resp or []:
            for msg_id, fields in messages:
                mid = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                raw = fields.get(b"data") or fields.get("data")
                yield mid, json.loads(raw)

    def reclaim_stale(self, consumer: str, min_idle_ms: int = 15 * 60 * 1000) -> list[tuple[str, dict]]:
        """죽은 워커가 잡고 있던 메시지를 회수한다."""
        self.ensure_group()
        _next, messages, *_ = self.redis.xautoclaim(self.stream, self.group, consumer, min_idle_ms, "0-0", count=10)
        out = []
        for msg_id, fields in messages:
            mid = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
            raw = fields.get(b"data") or fields.get("data")
            out.append((mid, json.loads(raw)))
        return out

    def ack(self, msg_id: str) -> None:
        self.redis.xack(self.stream, self.group, msg_id)

    def depth(self) -> dict[str, int]:
        self.ensure_group()
        group = next(g for g in self.redis.xinfo_groups(self.stream) if g["name"] in (self.group, self.group.encode()))
        last = group["last-delivered-id"]
        last = last.decode() if isinstance(last, bytes) else last
        return {
            "waiting": len(self.redis.xrange(self.stream, min=f"({last}", max="+")),
            "in_progress": group["pending"],
            "dead_letters": self.redis.xlen(self.dlq_stream),
        }

    def dead_letter(self, msg_id: str, payload: dict, error: str) -> None:
        self.redis.xadd(self.dlq_stream, {"data": json.dumps(payload, default=str), "error": error[:2000]})
        self.ack(msg_id)
