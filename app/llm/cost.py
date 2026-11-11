"""장애 1건 단위 LLM 비용 추적과 예산 차단."""
from datetime import datetime, timezone

from redis import Redis


class BudgetExceeded(Exception):
    pass


class CostTracker:
    def __init__(self, redis: Redis, prices: dict[str, list[float]], budget_usd: float):
        self.redis = redis
        self.prices = prices
        self.budget = budget_usd

    @staticmethod
    def _key(incident_id: str) -> str:
        return f"cost:{incident_id}"

    def estimate(self, model: str, tokens_in: int, tokens_out: int) -> float:
        p_in, p_out = self.prices.get(model, [0.0, 0.0])
        return (tokens_in * p_in + tokens_out * p_out) / 1_000_000

    def check_budget(self, incident_id: str) -> None:
        spent = float(self.redis.hget(self._key(incident_id), "usd") or 0)
        if spent >= self.budget:
            raise BudgetExceeded(f"incident {incident_id} spent ${spent:.4f} >= budget ${self.budget:.2f}")

    def record(self, incident_id: str, model: str, task: str, tokens_in: int, tokens_out: int) -> float:
        usd = self.estimate(model, tokens_in, tokens_out)
        key = self._key(incident_id)
        pipe = self.redis.pipeline()
        pipe.hincrbyfloat(key, "usd", usd)
        pipe.hincrby(key, "tokens_in", tokens_in)
        pipe.hincrby(key, "tokens_out", tokens_out)
        pipe.hincrby(key, f"calls:{model}", 1)
        pipe.hincrby(key, f"task:{task}", 1)
        pipe.expire(key, 30 * 86400)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        pipe.hincrbyfloat(f"cost:daily:{day}", model, usd)
        pipe.execute()
        return usd

    def record_cache_hit(self, incident_id: str, task: str) -> None:
        self.redis.hincrby(self._key(incident_id), f"cache_hit:{task}", 1)

    def summary(self, incident_id: str) -> dict:
        raw = self.redis.hgetall(self._key(incident_id))
        out: dict = {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            out[key] = round(float(val), 6) if key == "usd" else int(float(val))
        return out
