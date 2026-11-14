"""장애 1건 단위 LLM 비용 추적과 예산 차단. 호출 한 번이 llm_calls 한 줄이다."""
from psycopg_pool import ConnectionPool


class BudgetExceeded(Exception):
    pass


class CostTracker:
    def __init__(self, pool: ConnectionPool, prices: dict[str, list[float]], budget_usd: float):
        self.pool = pool
        self.prices = prices
        self.budget = budget_usd

    def estimate(self, model: str, tokens_in: int, tokens_out: int) -> float:
        p_in, p_out = self.prices.get(model, [0.0, 0.0])
        return (tokens_in * p_in + tokens_out * p_out) / 1_000_000

    def _spent(self, incident_id: str) -> float:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT coalesce(sum(usd), 0) AS usd FROM llm_calls WHERE incident_id = %s",
                               (incident_id,)).fetchone()
        return float(row["usd"])

    def check_budget(self, incident_id: str) -> None:
        spent = self._spent(incident_id)
        if spent >= self.budget:
            raise BudgetExceeded(f"incident {incident_id} spent ${spent:.4f} >= budget ${self.budget:.2f}")

    def record(self, incident_id: str, model: str, task: str, tokens_in: int, tokens_out: int) -> float:
        usd = self.estimate(model, tokens_in, tokens_out)
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO llm_calls (incident_id, task, model, tokens_in, tokens_out, usd) VALUES (%s, %s, %s, %s, %s, %s)",
                (incident_id, task, model, tokens_in, tokens_out, usd),
            )
        return usd

    def record_cache_hit(self, incident_id: str, task: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO llm_calls (incident_id, task, model, usd, cache_hit) VALUES (%s, %s, 'cache', 0, true)",
                (incident_id, task),
            )

    def summary(self, incident_id: str) -> dict:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """SELECT task, model, cache_hit, count(*) AS n, sum(usd) AS usd,
                          sum(coalesce(tokens_in, 0)) AS tin, sum(coalesce(tokens_out, 0)) AS tout
                   FROM llm_calls WHERE incident_id = %s GROUP BY task, model, cache_hit""",
                (incident_id,),
            ).fetchall()
        if not rows:
            return {}
        out: dict = {"usd": 0.0, "tokens_in": 0, "tokens_out": 0}
        for r in rows:
            if r["cache_hit"]:
                out[f"cache_hit:{r['task']}"] = out.get(f"cache_hit:{r['task']}", 0) + r["n"]
                continue
            out["usd"] += float(r["usd"])
            out["tokens_in"] += r["tin"]
            out["tokens_out"] += r["tout"]
            out[f"calls:{r['model']}"] = out.get(f"calls:{r['model']}", 0) + r["n"]
            out[f"task:{r['task']}"] = out.get(f"task:{r['task']}", 0) + r["n"]
        out["usd"] = round(out["usd"], 6)
        return out
