"""Pod 시작 전 의존성 대기(init container). 앱이 DB보다 먼저 뜨면 커넥션 풀이 재시도 간격을 늘리며
한동안 회복하지 못한다. 그래서 접속이 실제로 되는 것을 확인한 뒤에 앱을 띄운다.

  python -m app.wait            PostgreSQL, Redis 접속
  python -m app.wait --schema   위에 더해 마이그레이션 완료(체크포인트 테이블까지)
"""
import sys
import time

import psycopg
from redis import Redis

from app.config import get_settings

SCHEMA_READY = "SELECT 1 FROM alembic_version, checkpoint_migrations LIMIT 1"


def ready(schema: bool) -> str | None:
    s = get_settings()
    try:
        Redis.from_url(s.redis_url, socket_timeout=3).ping()
        with psycopg.connect(s.database_url, connect_timeout=3) as conn:
            if schema:
                conn.execute(SCHEMA_READY)
    except Exception as e:  # noqa: BLE001 - 준비되기 전에는 어떤 오류든 다시 기다린다
        return type(e).__name__
    return None


def main(timeout_sec: int = 300) -> None:
    schema = "--schema" in sys.argv
    deadline = time.monotonic() + timeout_sec
    while (reason := ready(schema)) and time.monotonic() < deadline:
        print(f"waiting: {reason}", flush=True)
        time.sleep(2)
    if reason:
        sys.exit(f"dependencies not ready after {timeout_sec}s: {reason}")
    print("ready", flush=True)


if __name__ == "__main__":
    main()
