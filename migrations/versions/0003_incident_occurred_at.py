"""장애가 실제로 시작된 시각(알림의 startsAt)을 남겨 감지 시간과 전달 시간을 잴 수 있게 한다.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute("ALTER TABLE incidents ADD COLUMN occurred_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE incidents DROP COLUMN occurred_at")
