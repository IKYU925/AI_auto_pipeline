"""PostgreSQL 연결과 스키마 관리."""
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("llm_calls", "actions", "approvals", "fixes", "documents", "incidents")


def sqlalchemy_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def make_pool(url: str) -> ConnectionPool:
    return ConnectionPool(url, min_size=1, max_size=5, kwargs={"row_factory": dict_row, "autocommit": True}, open=True)


def migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")


def ensure_database(url: str) -> None:
    """테스트, 데모용 데이터베이스가 없으면 만든다. 같은 서버의 기본 데이터베이스로 접속해서 만든다."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = conninfo_to_dict(url)["dbname"]
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(f'CREATE DATABASE "{name}"')


def truncate_all(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
