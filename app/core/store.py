"""장애 처리 상태 저장소(Redis Hash)."""
import json
from datetime import datetime, timezone

from redis import Redis


class IncidentStore:
    def __init__(self, redis: Redis, ttl_sec: int = 7 * 86400):
        self.redis = redis
        self.ttl = ttl_sec

    @staticmethod
    def _key(incident_id: str) -> str:
        return f"incident:{incident_id}"

    def set_status(self, incident_id: str, status: str, **extra) -> None:
        data = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        data.update({k: json.dumps(v, default=str) if not isinstance(v, str) else v for k, v in extra.items()})
        self.redis.hset(self._key(incident_id), mapping=data)
        self.redis.expire(self._key(incident_id), self.ttl)

    def get(self, incident_id: str) -> dict | None:
        raw = self.redis.hgetall(self._key(incident_id))
        if not raw:
            return None
        out = {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            try:
                out[key] = json.loads(val) if key not in ("status", "updated_at") else val
            except (json.JSONDecodeError, TypeError):
                out[key] = val
        return out

    def claim_fingerprint(self, fp: str, incident_id: str, ttl_sec: int) -> str | None:
        """처음 보는 지문이면 None, 이미 처리 중인 지문이면 기존 incident_id를 돌려준다."""
        key = f"dedupe:{fp}"
        if self.redis.set(key, incident_id, nx=True, ex=ttl_sec):
            return None
        existing = self.redis.get(key)
        return existing.decode() if isinstance(existing, bytes) else existing
