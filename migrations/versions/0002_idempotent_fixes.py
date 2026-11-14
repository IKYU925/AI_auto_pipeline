"""체크포인트에서 재개해 보고 단계가 다시 돌아도 같은 시도가 두 번 기록되지 않게 한다.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("ALTER TABLE fixes ADD CONSTRAINT fixes_attempt_once UNIQUE (incident_id, attempt, passed)")


def downgrade() -> None:
    op.execute("ALTER TABLE fixes DROP CONSTRAINT fixes_attempt_once")
