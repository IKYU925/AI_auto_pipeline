"""Alembic 실행 환경. 접속 주소는 앱 설정의 DATABASE_URL을 쓴다."""
from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.db import sqlalchemy_url

url = context.config.attributes.get("database_url") or get_settings().database_url
with create_engine(sqlalchemy_url(url)).connect() as conn:
    context.configure(connection=conn)
    with context.begin_transaction():
        context.run_migrations()
