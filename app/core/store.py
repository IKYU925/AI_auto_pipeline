"""장애 처리 상태 저장소. 영구 기록은 PostgreSQL, 중복 제거 키는 Redis."""
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from redis import Redis

TERMINAL = {"resolved", "escalated", "no_code_fix", "budget_exceeded", "failed"}


class IncidentStore:
    def __init__(self, pool: ConnectionPool, redis: Redis):
        self.pool = pool
        self.redis = redis

    def create(self, incident_id: str, fingerprint: str, incident: dict) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO incidents (id, fingerprint, source, service, severity, message, stacktrace, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued')""",
                (incident_id, fingerprint, incident.get("source", "api"), incident["service"], incident.get("severity"),
                 incident["message"], incident.get("stacktrace")),
            )

    def set_status(self, incident_id: str, status: str, *, attempts: int | None = None, report: str | None = None,
                   **result) -> None:
        cls = result.get("classification") or {}
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE incidents SET status = %s, updated_at = now(),
                     attempts = coalesce(%s, attempts), report = coalesce(%s, report),
                     category = coalesce(%s, category), needs_code_fix = coalesce(%s, needs_code_fix),
                     result = result || %s,
                     finished_at = CASE WHEN %s THEN now() ELSE finished_at END
                   WHERE id = %s""",
                (status, attempts, report, cls.get("category"), cls.get("needs_code_fix"), Jsonb(result),
                 status in TERMINAL, incident_id),
            )

    def get(self, incident_id: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT status, updated_at, fingerprint, attempts, report, result FROM incidents WHERE id = %s",
                (incident_id,),
            ).fetchone()
        if not row:
            return None
        out = {"status": row["status"], "updated_at": row["updated_at"].isoformat(), "fingerprint": row["fingerprint"],
               "attempts": row["attempts"], **row["result"]}
        if row["report"] is not None:
            out["report"] = row["report"]
        return out

    def report_ts(self, incident_id: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT report_ts FROM incidents WHERE id = %s", (incident_id,)).fetchone()
        return row["report_ts"] if row else None

    def set_report_ts(self, incident_id: str, ts: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE incidents SET report_ts = %s WHERE id = %s", (ts, incident_id))

    def claim_fingerprint(self, fp: str, incident_id: str, ttl_sec: int) -> str | None:
        """처음 보는 지문이면 None, 이미 처리 중인 지문이면 기존 incident_id를 돌려준다."""
        key = f"dedupe:{fp}"
        if self.redis.set(key, incident_id, nx=True, ex=ttl_sec):
            return None
        existing = self.redis.get(key)
        return existing.decode() if isinstance(existing, bytes) else existing


class FixStore:
    """검증 시도마다 한 줄. 재사용은 같은 지문의 가장 최근 통과 수정안 중 아직 배포되지 않은(retired_at 없음) 것."""

    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def record(self, incident_id: str, fingerprint: str, attempt: int, fix: dict, test_summary: str, passed: bool) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO fixes (incident_id, fingerprint, attempt, patch, explanation, risk, test_summary, passed)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT ON CONSTRAINT fixes_attempt_once DO NOTHING""",
                (incident_id, fingerprint, attempt, fix["patch"], fix.get("explanation"), fix.get("risk"),
                 test_summary, passed),
            )

    def latest_passed(self, fingerprint: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                """SELECT patch, explanation, risk FROM fixes WHERE fingerprint = %s AND passed AND retired_at IS NULL
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (fingerprint,),
            ).fetchone()
        return dict(row) if row else None

    def retire_all(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE fixes SET retired_at = now() WHERE retired_at IS NULL")
